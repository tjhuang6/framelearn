"""Vision Agent evaluator for keyframe selection.

Implements a single-agent tool-calling loop where the Vision model can
request re-captures at different timestamps before committing to a
keep/discard decision.

Loop per candidate frame:
  observe (current frame + subtitle) → decide tool call OR
  → capture_frame tool call → observe (new frame) → ... → decide
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from framelearn.config import get as config_get
from framelearn.pipeline.ffmpeg_helper import FFmpegHelper
from framelearn.provider_adapter import (
    ProviderConfig,
    call_llm_with_tools,
    encode_image,
    load_vision_config,
)


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_CAPTURE_FRAME: dict = {
    "type": "function",
    "function": {
        "name": "capture_frame",
        "description": (
            "截取视频指定时间点的帧。当当前帧模糊、处于画面过渡中或不具代表性时使用。"
            "截取后新帧会直接展示给你。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timestamp": {
                    "type": "number",
                    "description": "要截取的视频时间点（秒）。",
                }
            },
            "required": ["timestamp"],
        },
    },
}

TOOL_DECIDE: dict = {
    "type": "function",
    "function": {
        "name": "decide",
        "description": (
            "提交对当前帧的最终保留或丢弃决策。当你有足够信息做出判断时调用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keep": {
                    "type": "boolean",
                    "description": "true 表示保留此帧用于教材，false 表示丢弃。",
                },
                "reason": {
                    "type": "string",
                    "description": "决策理由（简短说明）。",
                },
            },
            "required": ["keep", "reason"],
        },
    },
}

_TOOLS = [TOOL_CAPTURE_FRAME, TOOL_DECIDE]

_SYSTEM_PROMPT = (
    "你是一个视频教材关键帧评估助手。你的任务是判断一个视频帧是否值得保留在教材中。\n\n"
    "保留标准：\n"
    "- 画面包含 PPT、代码、终端、图表、公式、操作界面 → 保留\n"
    "- 字幕提到“如图”、“看代码”、“这里”等指向画面的表达 → 保留\n\n"
    "丢弃标准：\n"
    "- 画面主要是讲师人脸、过渡动画、空白屏、纯背景 → 丢弃\n"
    "- 画面内容与字幕无关或信息量低 → 丢弃\n\n"
    "如果当前帧质量差，可以调用 capture_frame 请求重新截帧。"
    "完成判断后，调用 decide 提交结论。"
)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class VisionAgentEvaluator:
    """Single-agent tool-calling loop for keyframe quality evaluation.

    The Vision model observes the current frame, optionally requests
    re-captures via capture_frame, and commits a final decision via decide.
    """

    def __init__(self) -> None:
        # 2.2: read max_retries from config, default 5
        self.max_retries: int = int(config_get("runtime.vision_agent_max_retries", 5))
        self._config: ProviderConfig = load_vision_config()

    def evaluate(
        self,
        frame_path: Path,
        context: str,
        video_path: str,
        output_dir: Path,
        initial_timestamp: float,
    ) -> "KeyframeEvaluation":
        """Run the agent loop to evaluate a single keyframe.

        Args:
            frame_path: Path to the initially captured frame.
            context: Subtitle text for this segment.
            video_path: Path to the source video (for re-captures).
            output_dir: Directory for any re-captured frames.
            initial_timestamp: Timestamp of the initial frame (seconds).

        Returns:
            KeyframeEvaluation with keep/discard decision and reason.
        """
        # 2.3: build initial message history with first frame
        user_parts: list[dict] = [
            {"type": "text", "text": (
                f"视频字幕上下文：\"{context[:200]}\"\n\n"
                "请查看这张视频帧，判断是否值得保留在教材中。"
            )},
        ]
        try:
            b64, mime = encode_image(str(frame_path))
            user_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        except Exception:
            return _make_eval(keep=True, reason="图像加载失败，默认保留")

        messages: list[dict] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_parts},
        ]

        retries = 0
        # 2.4: loop until decide or max_retries
        while True:
            response_body = call_llm_with_tools(
                messages=messages,
                tools=_TOOLS,
                config=self._config,
                max_tokens=512,
                timeout=60,
            )

            assistant_msg: dict = response_body["choices"][0]["message"]
            messages.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                return _make_eval(keep=True, reason="模型未调用工具，默认保留")

            tc = tool_calls[0]
            tool_name: str = tc["function"]["name"]
            try:
                args: dict = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                return _make_eval(keep=True, reason="工具参数解析失败，默认保留")

            if tool_name == "decide":
                # 2.6: terminal — return evaluation
                return self._handle_decide(args)

            if tool_name == "capture_frame":
                if retries >= self.max_retries:
                    # max retries reached — conservative keep
                    return _make_eval(
                        keep=True,
                        reason=f"达到最大重试次数（{self.max_retries}），保守保留",
                    )
                # 2.5: capture new frame and append result to history
                new_frame, result_text = self._handle_capture_frame(
                    timestamp=float(args.get("timestamp", initial_timestamp)),
                    video_path=video_path,
                    output_dir=Path(output_dir),
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_text,
                })
                if new_frame is not None:
                    try:
                        b64, mime = encode_image(str(new_frame))
                        messages.append({
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "这是重新截取的帧，请继续评估。"},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                                },
                            ],
                        })
                    except Exception:
                        messages.append({
                            "role": "user",
                            "content": "新帧图像加载失败，请基于已有信息作出决策。",
                        })
                retries += 1
                continue

            # Unknown tool — terminate gracefully
            return _make_eval(keep=True, reason=f"未知工具 '{tool_name}'，默认保留")

    # ── internal helpers ───────────────────────────────────────────────

    def _handle_capture_frame(
        self,
        timestamp: float,
        video_path: str,
        output_dir: Path,
    ) -> "tuple[Optional[Path], str]":
        """Capture a frame at the given timestamp.

        Returns:
            (frame_path, result_text) — frame_path is None on failure.
        """
        h = int(timestamp // 3600)
        m = int((timestamp % 3600) // 60)
        s = int(timestamp % 60)
        frame_name = f"frame_agent_{h:02d}h{m:02d}m{s:02d}s.jpg"
        frame_path = output_dir / frame_name

        success = FFmpegHelper.capture_single_frame(video_path, timestamp, str(frame_path))
        if success and frame_path.exists():
            return frame_path, f"已在 {timestamp:.1f}s 截取新帧：{frame_name}"
        return None, f"截帧失败（{timestamp:.1f}s），请基于已有信息作出决策。"

    def _handle_decide(self, args: dict) -> "KeyframeEvaluation":
        """Parse decide tool args and return evaluation."""
        return _make_eval(
            keep=bool(args.get("keep", True)),
            reason=str(args.get("reason", "")),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_eval(keep: bool, reason: str) -> "KeyframeEvaluation":
    """Import KeyframeEvaluation lazily to avoid circular imports."""
    from framelearn.pipeline.agent_keyframe_selector import KeyframeEvaluation
    return KeyframeEvaluation(keep=keep, reason=reason)
