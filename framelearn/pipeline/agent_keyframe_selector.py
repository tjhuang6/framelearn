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
        self.vision_mode = config_get("runtime.vision_mode", "appserver")
        self.vision_provider = config_get("runtime.vision_provider", "deepseek")
        self.vision_model = config_get("runtime.vision_model", "deepseek-reasoner")

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

            # Step 3: Capture frame with FFmpeg
            h = int(ts // 3600)
            m = int((ts % 3600) // 60)
            s = int(ts % 60)
            frame_name = f"frame_{h:02d}h{m:02d}m{s:02d}s.jpg"
            frame_path = output_dir / frame_name

            print(f"   📸 补帧 {frame_name}（{seg.text[:30]}...）")
            success = FFmpegHelper.capture_single_frame(
                video_path, ts, str(frame_path)
            )
            if not success:
                print(f"   ⚠️  截帧失败：{frame_name}")
                continue

            # Step 4: LLM evaluates the image
            evaluation = self._evaluate(frame_path, seg.text)
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
        except Exception:
            # Fallback: trust the heuristic
            return KeyframeDecision(
                timestamp=seg.start,
                need_frame=True,
                reason="LLM 决策失败，启发式规则触发",
            )

    def _evaluate(self, frame_path: Path, context: str) -> KeyframeEvaluation:
        """Ask LLM: is this frame worth keeping, using both image and subtitle context."""
        prompt = (
            f"视频关键帧（对应字幕：\"{context[:200]}\"）\n\n"
            "结合画面内容和字幕，判断这张截图是否值得保留在教材中：\n"
            "- 画面包含 PPT、代码、终端、图表、公式、操作界面 → 保留\n"
            "- 字幕提到'如图'、'看代码'、'这里'等指向画面的表达 → 保留\n"
            "- 画面主要是讲师人脸、过渡动画、空白屏、纯背景 → 丢弃\n"
            "- 画面内容与字幕无关或信息量低 → 丢弃\n\n"
            "返回 JSON（只返回 JSON，不要其他内容）：\n"
            "{\"keep\": true/false, \"reason\": \"理由\"}"
        )

        try:
            response = self._call_vision_llm(prompt, frame_path)
            data = json.loads(response.strip())
            return KeyframeEvaluation(
                keep=bool(data.get("keep", True)),
                reason=data.get("reason", ""),
            )
        except Exception:
            # Fallback to text-only evaluation on failure
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
        except Exception:
            return KeyframeEvaluation(keep=True, reason="评估失败，默认保留")

    def _call_text_llm(self, prompt: str) -> str:
        """Call text LLM for decision-making (fast, no images)."""
        if self.vision_mode == "appserver":
            from framelearn.app_server.session import AppServerSession
            session = AppServerSession(workspace=".")
            result = session.run_turn(prompt)
            session.close()
            return result.final_text or "{}"
        else:
            from framelearn.provider_adapter import call_text_llm
            return call_text_llm(prompt, max_tokens=200)

    def _call_vision_llm(self, prompt: str, frame_path: Path) -> str:
        """Call vision LLM with text + image."""
        if self.vision_mode == "appserver":
            from framelearn.app_server.session import AppServerSession
            session = AppServerSession(workspace=".")
            result = session.run_turn(prompt)
            session.close()
            return result.final_text or ""
        else:
            import os

            from framelearn.provider_adapter import PROVIDERS, ProviderConfig, call_llm

            provider_def = PROVIDERS.get(self.vision_provider)
            if not provider_def:
                raise ValueError(f"Unknown vision_provider: '{self.vision_provider}'")

            if self.vision_provider == "siliconflow":
                api_key = os.getenv("VISION_API_KEY") or os.getenv("SILICONFLOW_API_KEY", "")
                base_url = (
                    os.getenv("VISION_BASE_URL")
                    or os.getenv("SILICONFLOW_BASE_URL", provider_def["base_url"])
                )
            else:
                api_key = os.getenv("VISION_API_KEY", "")
                base_url = os.getenv("VISION_BASE_URL", provider_def["base_url"])

            config = ProviderConfig(
                provider=self.vision_provider,
                api_key=api_key,
                model=self.vision_model,
                base_url=base_url,
            )
            return call_llm(
                prompt,
                config,
                images=[str(frame_path)],
                max_tokens=200,
            )
