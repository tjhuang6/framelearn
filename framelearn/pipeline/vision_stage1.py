"""VisionStage1 — first vision-model call.

Inputs:
    - A cleaned SRT chunk
    - The candidate frames the heuristic extractor placed in this chunk

Outputs:
    - ``blog_markdown``: a blog-style prose rendering of the chunk
    - ``selected_timestamps``: ≤ 50 timestamps the vision model wants
      kept, augmented, or modified. Each item can either reuse a
      heuristic frame (``needs_extract=False``) or request a fresh
      extraction at an adjusted/new timestamp (``needs_extract=True``).

Stage2 takes ``selected_timestamps`` plus the actual frames (heuristic +
newly extracted) and decides which ones are worth keeping visually.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from framelearn.config import get as config_get
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.run_report import get_reporter
from framelearn.pipeline.srt_chunker import SRTChunk
from framelearn.provider_adapter import (
    ProviderConfig,
    call_llm_async,
    load_vision_config,
)


STAGE1_PROMPT = """你是视频字幕整理助手。会给你一份"已配图的 SRT markdown"——字幕段按时间顺序排好，每段后面可能跟着一张或多张该时间点的启发式截图（用 `![](path)` 标记）。

## 任务

请做三件事：

1. **生成markdown**：合并 SRT 段为连贯的叙述段落，去掉时间戳和序号，使表达通畅。尽量不要出现第一人称。

2. **决定每张启发式截图的去留 / 调整 / 重截 / 删除**：
   - 内容对得上 + 时间点对 → 保留（needs_extract=false，source_frame_path 引用 SRT_MD 里出现过的图片路径）
   - 时间点差 ±2 秒 → 调整 timestamp，source_frame_path 仍指向同一张图
   - 内容真不行（黑屏、过渡帧、模糊）→ 重截（needs_extract=true，source_frame_path=null，给新 timestamp）
   - 截屏多余,或者质量太差 -> 删除（needs_extract=false，source_frame_path=null）

3. **新增截图**（可选）：启发式漏了老师提到的关键图（PPT / 代码 / 表格 / 屏幕），needs_extract=true + 新 timestamp。

## 输入

<SRT_MD>
{chunk_text}
</SRT_MD>

## 输出 JSON（严格格式，不要解释）

{{
  "blog_markdown": "## 标题\n\n[博客式段落...]",
  "selected_timestamps": [
    {{"srt_id": <int>, "timestamp": <float seconds>, "needs_extract": <bool>, "source_frame_path": "<path|null>", "reason": "<string>"}},
    ...
  ]
}}

约束：
- selected_timestamps 数量 ≤ {max_images}
- needs_extract=true 时 source_frame_path 必须是 null
- needs_extract=false 时 source_frame_path 必须是输入 SRT_MD 里 `![](...)` 出现过的图片路径
- timestamp 允许相对启发式帧调整 ±2 秒
- 不要输出 markdown 之外的解释文字
"""


@dataclass
class SelectedTimestamp:
    srt_id: int
    timestamp: float
    needs_extract: bool
    source_frame_path: str | None
    reason: str


@dataclass
class VisionStage1Output:
    blog_markdown: str
    selected_timestamps: list[SelectedTimestamp]


def _format_srt(segments: Iterable) -> str:
    """Render SRT segments as numbered lines for the prompt."""
    lines = []
    for i, seg in enumerate(segments, start=1):
        text = getattr(seg, "text", "") or ""
        lines.append(f"{i}. {text}")
    return "\n".join(lines)


def _format_frames(frames: list[CandidateFrame]) -> str:
    """Render frames as JSON for the prompt."""
    items = [
        {
            "srt_id_hint": i + 1,  # stage1 maps heuristic frame index → segment index
            "timestamp_sec": f.timestamp_sec,
            "path": f.path,
        }
        for i, f in enumerate(frames)
    ]
    return json.dumps(items, ensure_ascii=False, indent=2)


def _parse_stage1(raw: str, frames: list[CandidateFrame]) -> VisionStage1Output | None:
    """Parse LLM response into VisionStage1Output, or None on failure."""
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
    blog = data.get("blog_markdown")
    selected = data.get("selected_timestamps")
    if not isinstance(blog, str) or not isinstance(selected, list):
        return None

    frame_paths = {f.path for f in frames}
    parsed: list[SelectedTimestamp] = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        try:
            srt_id = int(item["srt_id"])
            timestamp = float(item["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        needs_extract = bool(item.get("needs_extract", False))
        src = item.get("source_frame_path")
        reason = str(item.get("reason", ""))

        if needs_extract:
            src = None
        elif src not in frame_paths:
            # LLM claimed to reuse a heuristic frame but the path doesn't
            # match any — treat as needs_extract.
            needs_extract = True
            src = None

        parsed.append(
            SelectedTimestamp(
                srt_id=srt_id,
                timestamp=timestamp,
                needs_extract=needs_extract,
                source_frame_path=src,
                reason=reason,
            )
        )
    return VisionStage1Output(blog_markdown=blog, selected_timestamps=parsed)


def _fallback_output(chunk: SRTChunk, frames: list[CandidateFrame]) -> VisionStage1Output:
    """Conservative fallback when Stage1 keeps failing.

    blog_markdown = concatenated chunk text. selected_timestamps = every
    heuristic frame reused as-is (``needs_extract=False``).
    """
    blog = "\n\n".join(
        getattr(seg, "text", "") for seg in chunk.segments if getattr(seg, "text", "")
    )
    selected = [
        SelectedTimestamp(
            srt_id=i + 1,
            timestamp=f.timestamp_sec,
            needs_extract=False,
            source_frame_path=f.path,
            reason="heuristic fallback",
        )
        for i, f in enumerate(frames)
    ]
    return VisionStage1Output(blog_markdown=blog, selected_timestamps=selected)


class VisionStage1:
    """Call the vision model with text + heuristic frames for one chunk."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        max_images: int | None = None,
        max_retries: int = 2,
        timeout: int = 600,
    ):
        self.config = config or load_vision_config()
        self.max_images = (
            max_images
            if max_images is not None
            else int(config_get("chunking.max_images_per_chunk", 50))
        )
        self.max_retries = max_retries
        self.timeout = timeout

    async def process(
        self,
        chunk: SRTChunk,
        frames_in_chunk: list[CandidateFrame],
    ) -> VisionStage1Output:
        """Run Stage1 for one chunk. Returns a fallback on final failure."""
        prompt = STAGE1_PROMPT.format(
            chunk_text=_format_srt(chunk.segments),
            frames_json=_format_frames(frames_in_chunk),
            max_images=self.max_images,
        )
        image_paths = [f.path for f in frames_in_chunk]

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await call_llm_async(
                    prompt,
                    self.config,
                    images=image_paths,
                    max_tokens=8192,
                    timeout=self.timeout,
                )
                parsed = _parse_stage1(response, frames_in_chunk)
                if parsed is None:
                    raise ValueError("Stage1 response did not match schema")
                # Enforce the ≤ max_images cap — keep the first N the model
                # ranked highest (it has no explicit rank field, so just
                # take the leading slice; downstream keeps decisions sane).
                if len(parsed.selected_timestamps) > self.max_images:
                    parsed.selected_timestamps = parsed.selected_timestamps[: self.max_images]
                return parsed
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

        get_reporter().record_fallback(
            "vision_stage1.fallback",
            f"chunk {chunk.index} Stage1 失败（{last_error}），已降级到启发式保留",
        )
        return _fallback_output(chunk, frames_in_chunk)


def extract_new_frames(
    selected: list[SelectedTimestamp],
    video_path: str,
    chunk_index: int,
    output_dir: Path,
) -> list[CandidateFrame]:
    """FFmpeg-capture every ``needs_extract=True`` selection.

    Output paths: ``<output_dir>/chunk_<chunk_index>/extra_frame_<j>.jpg``.
    Skipped timestamps are not fatal — we keep going.
    """
    from framelearn.pipeline.ffmpeg_helper import FFmpegHelper

    output_dir = Path(output_dir)
    chunk_dir = output_dir / f"chunk_{chunk_index}"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    new_frames: list[CandidateFrame] = []
    for j, sel in enumerate(selected):
        if not sel.needs_extract:
            continue
        target = chunk_dir / f"extra_frame_{j:03d}.jpg"
        ok = FFmpegHelper.capture_single_frame(
            video_path, sel.timestamp, str(target)
        )
        if ok:
            new_frames.append(
                CandidateFrame(
                    path=str(target),
                    timestamp_sec=sel.timestamp,
                    source="stage1",
                )
            )
        else:
            get_reporter().record_skipped_frame(
                "vision_stage1.extract_new_frames",
                f"无法在 {sel.timestamp}s 截帧",
                detail={"chunk": chunk_index, "timestamp": sel.timestamp},
            )
    return new_frames