"""Agent-driven keyframe selector.

Instead of extracting all frames upfront and deduplicating, this module
uses an LLM to decide which moments in the video need a screenshot,
captures only those frames, and evaluates whether each one is worth keeping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from framelearn.config import get as config_get
from framelearn.pipeline.asr_adapter import TranscriptSegment
from framelearn.pipeline.ffmpeg_helper import FFmpegHelper
from framelearn.pipeline.run_report import get_reporter
from framelearn.pipeline.vision_agent import VisionAgentEvaluator


# Keywords that suggest a screenshot is needed
_SCREENSHOT_KEYWORDS = [
    "看", "如图", "图中", "屏幕", "代码", "演示", "这里", "PPT",
    "展示", "可以看到", "注意", "看一下", "画面", "截图",
]


@dataclass
class KeyframeDecision:
    timestamp: float
    reason: str
    need_frame: bool


@dataclass
class KeyframeEvaluation:
    keep: bool
    reason: str


class AgentKeyframeSelector:
    """LLM-driven keyframe selection loop.

    Flow per subtitle segment:
    1. Heuristic pre-filter (cheap): does the text mention visual content?
    2. LLM decision (if heuristic passes): confirm and reason
    3. FFmpeg captures the frame
    4. LLM evaluates the image: keep (PPT/code/terminal) or discard (face/blank)
    """

    def __init__(self):
        self.vision_mode = config_get("vision.vision_mode", "appserver")
        self.vision_provider = config_get("vision.vision_provider", "deepseek")
        self.vision_model = config_get("vision.vision_model", "deepseek-reasoner")

    def select(
        self,
        video_path: str,
        segments: list[TranscriptSegment],
        output_dir: Path,
        existing_keyframes: list[tuple[Path, float]] | None = None,
    ) -> list[tuple[Path, float]]:
        """Select keyframes by LLM decision loop.

        Args:
            video_path: Path to the source video
            segments: Subtitle segments with timestamps
            output_dir: Directory to save selected frames
            existing_keyframes: Optional existing frames to merge with

        Returns:
            List of (frame_path, timestamp_seconds) tuples
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        selected: list[tuple[Path, float]] = list(existing_keyframes or [])
        existing_ts = {ts for _, ts in selected}

        for seg in segments:
            # Skip segments without timestamps
            if seg.start is None or seg.end is None:
                continue

            # Step 1: Heuristic pre-filter (free)
            if not self._heuristic_needs_frame(seg.text):
                continue

            # Step 2: LLM decides whether a frame is needed
            decision = self._decide(seg)
            if not decision.need_frame:
                continue

            # Avoid duplicate timestamps (±2 seconds)
            ts = decision.timestamp
            if any(abs(ts - e) < 2.0 for e in existing_ts):
                continue

            # Step 3: Capture frame with FFmpeg (with millisecond precision + agent tag)
            h = int(ts // 3600)
            m = int((ts % 3600) // 60)
            s = int(ts % 60)
            ms = round((ts % 1) * 1000)
            frame_name = f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_agent_{len(selected)+1:03d}.jpg"
            frame_path = output_dir / frame_name

            print(f"   📸 补帧 {frame_name}（{seg.text[:30]}...）")
            success = FFmpegHelper.capture_single_frame(
                video_path, ts, str(frame_path)
            )
            if not success:
                print(f"   ⚠️  截帧失败：{frame_name}")
                get_reporter().record_skipped_frame(
                    "agent_keyframe_selector",
                    f"截帧失败，已跳过：{frame_name}",
                    detail={"timestamp": ts, "subtitle": seg.text[:60]},
                )
                continue

            # Step 4: Vision agent evaluates the image (tool-calling loop)
            evaluation = self._evaluate(frame_path, seg.text, video_path, output_dir, ts)
            if evaluation.keep:
                selected.append((frame_path, ts))
                existing_ts.add(ts)
                print(f"   ✅ 保留：{frame_name}（{evaluation.reason}）")
            else:
                frame_path.unlink(missing_ok=True)
                print(f"   ❌ 丢弃：{frame_name}（{evaluation.reason}）")

        # Sort by timestamp
        selected.sort(key=lambda x: x[1])
        return selected

    # ── helpers ────────────────────────────────────────────────────────────

    def _heuristic_needs_frame(self, text: str) -> bool:
        """Quick keyword check before calling LLM."""
        return any(kw in text for kw in _SCREENSHOT_KEYWORDS)

    def _decide(self, seg: TranscriptSegment) -> KeyframeDecision:
        """Ask LLM: does this subtitle segment need a screenshot?"""
        prompt = (
            f"视频字幕片段：\n\"{seg.text}\"\n\n"
            "这段字幕是否需要截图？判断依据：\n"
            "- 提到'看图'、'如图'、'代码'、'屏幕'、'演示'、'PPT' → 需要\n"
            "- 只是口头讲解，无参考内容 → 不需要\n\n"
            "返回 JSON（只返回 JSON，不要其他内容）：\n"
            "{\"need_frame\": true/false, \"reason\": \"理由\"}"
        )

        try:
            response = self._call_text_llm(prompt)
            data = json.loads(response.strip())
            return KeyframeDecision(
                timestamp=seg.start,
                need_frame=bool(data.get("need_frame", False)),
                reason=data.get("reason", ""),
            )
        except Exception as e:
            # Fallback: trust the heuristic
            get_reporter().record_fallback(
                "agent_keyframe_selector.decide",
                f"LLM 决策失败，启发式规则触发（补帧）：{e}",
                detail={"subtitle": seg.text[:60]},
            )
            return KeyframeDecision(
                timestamp=seg.start,
                need_frame=True,
                reason="LLM 决策失败，启发式规则触发",
            )

    def _evaluate(
        self,
        frame_path: Path,
        context: str,
        video_path: str,
        output_dir: Path,
        timestamp: float,
    ) -> KeyframeEvaluation:
        """Run the Vision agent tool-calling loop to evaluate a keyframe.

        The agent may request re-captures before committing to keep/discard.
        Falls back to text-only evaluation if the agent loop raises.
        """
        try:
            evaluator = VisionAgentEvaluator()
            return evaluator.evaluate(frame_path, context, video_path, output_dir, timestamp)
        except Exception as e:
            # Fallback to text-only evaluation on failure
            get_reporter().record_fallback(
                "agent_keyframe_selector.evaluate",
                f"视觉评估失败，降级为文字评估：{e}",
                detail={"frame": str(frame_path), "subtitle": context[:60]},
            )
            return self._evaluate_text_only(context)

    def _evaluate_text_only(self, context: str) -> KeyframeEvaluation:
        """Fallback: evaluate using subtitle text only (no image)."""
        prompt = (
            f"视频片段的字幕内容：\n\"{context[:200]}\"\n\n"
            "根据这段字幕，判断此时的画面是否值得截图保留在教材中：\n"
            "- 字幕提到代码、PPT、图表、终端命令、具体操作 → 保留\n"
            "- 字幕只是纯口头讲解、过渡语句、寒暄或背景介绍 → 丢弃\n\n"
            "返回 JSON（只返回 JSON，不要其他内容）：\n"
            "{\"keep\": true/false, \"reason\": \"理由\"}"
        )
        try:
            response = self._call_text_llm(prompt)
            data = json.loads(response.strip())
            return KeyframeEvaluation(
                keep=bool(data.get("keep", True)),
                reason=f"[文字fallback] {data.get('reason', '')}",
            )
        except Exception as e:
            get_reporter().record_fallback(
                "agent_keyframe_selector.evaluate_text_only",
                f"视觉与文字评估均失败，默认保留该帧：{e}",
                detail={"subtitle": context[:60]},
            )
            return KeyframeEvaluation(keep=True, reason="评估失败，默认保留")

    def _call_text_llm(self, prompt: str) -> str:
        """Call text LLM for decision-making (fast, no images)."""
        from framelearn.provider_adapter import call_text_llm
        return call_text_llm(prompt, max_tokens=200)
