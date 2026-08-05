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
    "- 字幕提到"如图"、"看代码"、"这里"等指向画面的表达 → 保留\n\n"
    "丢弃标准：\n"
    "- 画面主要是讲师人脸、过渡动画、空白屏、纯背景 → 丢弃\n"
    "- 画面内容与字幕无关或信息量低 → 丢弃\n\n"
    "如果当前帧质量差，可以调用 capture_frame 请求重新截帧。"
    "完成判断后，调用 decide 提交结论。"
)
