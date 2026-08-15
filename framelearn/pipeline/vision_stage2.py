"""VisionStage2 — final keep/discard decision.

The vision model has now actually SEEN every selected frame (heuristic +
Stage1-requested). It returns a per-frame decision. We use these
decisions downstream to assemble ``srt_picture.md`` and ``blog.md``.

On failure we conservatively keep every frame.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Iterable

from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.run_report import get_reporter
from framelearn.pipeline.srt_chunker import SRTChunk
from framelearn.provider_adapter import (
    ProviderConfig,
    call_llm_async,
    load_vision_config,
)


STAGE2_PROMPT = """你是视频关键帧筛选助手。你会看到 N 张关键帧和对应字幕段文本。

为每张图决定保留（keep=true）还是丢弃（keep=false）。

## 判断标准

**保留**（有视觉教学价值）：
- PPT 幻灯片（标题页 / 章节封面）
- 代码（IDE、编辑器）
- 终端 / 命令行输出
- 表格、流程图、示意图
- 公式、屏幕演示

**丢弃**（无视觉教学价值）：
- 讲师人脸 / 头像特写
- 模糊画面 / 纯黑屏 / 纯白屏
- 空白屏幕（与字幕内容不相关）
- 几乎相同的两张（保留更清晰的那张）

## 输入

字幕段：
{subtitle_text}

帧列表（path 字段为图片文件名）：
{frames_json}

## 输出 JSON（严格格式，不要解释）

{{
  "decisions": [
    {{"frame": "<path>", "keep": <bool>, "reason": "<string>"}},
    ...
  ]
}}

约束：
- decisions 数量 = 帧数量
- frame 字段必须是帧列表中的 path 之一
- 不要输出 markdown 之外的解释文字
"""


@dataclass
class FrameDecision:
    srt_id: int
    frame_path: str
    timestamp: float
    keep: bool
    reason: str


def _format_srt(segments: Iterable) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        text = getattr(seg, "text", "") or ""
        lines.append(f"{i}. {text}")
    return "\n".join(lines)


def _format_frames(frames: list[CandidateFrame], srt_id_per_frame: list[int]) -> str:
    """Render frames as JSON. ``srt_id_per_frame[i]`` is the SRT segment id for frame[i]."""
    items = [
        {
            "path": f.path,
            "timestamp_sec": f.timestamp_sec,
            "srt_id": srt_id,
        }
        for f, srt_id in zip(frames, srt_id_per_frame)
    ]
    return json.dumps(items, ensure_ascii=False, indent=2)


def _parse_stage2(
    raw: str, frames: list[CandidateFrame], srt_id_per_frame: list[int]
) -> list[FrameDecision] | None:
    """Parse LLM response into per-frame decisions, or None on failure."""
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    candidate = fenced.group(1) if fenced else raw
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        return None
    if len(decisions) != len(frames):
        return None

    path_to_meta = {
        f.path: (srt_id_per_frame[i], f.timestamp_sec)
        for i, f in enumerate(frames)
    }
    parsed: list[FrameDecision] = []
    for item in decisions:
        if not isinstance(item, dict):
            return None
        path = item.get("frame")
        keep = bool(item.get("keep", False))
        reason = str(item.get("reason", ""))
        if path not in path_to_meta:
            return None
        srt_id, ts = path_to_meta[path]
        parsed.append(
            FrameDecision(
                srt_id=srt_id,
                frame_path=path,
                timestamp=ts,
                keep=keep,
                reason=reason,
            )
        )
    return parsed


def _fallback_keep_all(
    frames: list[CandidateFrame], srt_id_per_frame: list[int]
) -> list[FrameDecision]:
    return [
        FrameDecision(
            srt_id=srt_id_per_frame[i],
            frame_path=f.path,
            timestamp=f.timestamp_sec,
            keep=True,
            reason="fallback keep-all",
        )
        for i, f in enumerate(frames)
    ]


class VisionStage2:
    """Call the vision model with cleaned SRT + every selected frame."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        max_retries: int = 2,
        timeout: int = 600,
    ):
        self.config = config or load_vision_config()
        self.max_retries = max_retries
        self.timeout = timeout

    async def process(
        self,
        chunk: SRTChunk,
        all_frames: list[CandidateFrame],
        srt_id_per_frame: list[int],
    ) -> list[FrameDecision]:
        """Decide keep/discard for each frame. Returns fallback on failure.

        Args:
            chunk: The cleaned SRT chunk (used as context for decisions).
            all_frames: Heuristic frames Stage1 kept + frames Stage1 asked
                to extract. Order matters — it's how we map decisions
                back to frame paths.
            srt_id_per_frame: For each frame, the SRT segment id the
                Stage1 model associated it with.
        """
        if not all_frames:
            return []

        prompt = STAGE2_PROMPT.format(
            subtitle_text=_format_srt(chunk.segments),
            frames_json=_format_frames(all_frames, srt_id_per_frame),
        )
        image_paths = [f.path for f in all_frames]

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await call_llm_async(
                    prompt,
                    self.config,
                    images=image_paths,
                    max_tokens=4096,
                    timeout=self.timeout,
                )
                parsed = _parse_stage2(response, all_frames, srt_id_per_frame)
                if parsed is None:
                    raise ValueError("Stage2 response did not match schema")
                return parsed
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

        get_reporter().record_fallback(
            "vision_stage2.fallback",
            f"chunk {chunk.index} Stage2 失败（{last_error}），全部 keep=true",
        )
        return _fallback_keep_all(all_frames, srt_id_per_frame)