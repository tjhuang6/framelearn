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
    call_llm_async_interleaved,
    load_vision_config,
)


STAGE1_PROMPT = """你是视频字幕整理助手。会给你一份"已配图的 SRT markdown"——字幕段按时间顺序排好，每段后面可能跟着一张或多张该时间点的启发式截图，**每张截图紧跟一行 markdown 标记** `![picture N](path)` 和时间戳，方便你把图和上下文配对（vision API 会按"图-标记-图-标记"的顺序发给你）。

## 任务

请做三件事：

1. **生成博客 markdown**：合并所有字幕段为连贯的叙述段落，去掉时间戳和序号，使表达通畅。尽量不要出现第一人称。

2. **决定每张 picture N 的去留**：
   - 内容对得上 + 时间点对 → **保留**（needs_extract=false，source_frame_path 写成 markdown 里出现过的那个 `path`，srt_id 取最近的段号）
   - 时间点差 ±2 秒 → **调整 timestamp**（source_frame_path 仍指向同一张图，timestamp 用更准的秒数）
   - 内容真不行（黑屏、过渡帧、模糊）→ **重截**（needs_extract=true，source_frame_path=null，给新 timestamp）
   - 截屏多余或质量太差 → **删除**（needs_extract=false，source_frame_path=null，从列表里去掉这张图）

3. **新增截图**（可选）：启发式漏了老师提到的关键图（PPT / 代码 / 表格 / 屏幕），needs_extract=true + 新 timestamp。

## 输入

<SRT_MD>
{chunk_text}
</SRT_MD> 

## 输出 下述json格式 不要有任何多余的解释

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



## 真实例子（参考这一个范例的输入输出结构）

输入 SRT_MD 长这样（vision API 会把每张图按位置插到对应 markdown 行旁边）：

<SRT_MD>
1. 老师讲到 RGB 三通道，一张宽 224 的图片是 3×224×224，三层数字矩阵对应 RGB。

![picture 1](src/frame_00h00m00s000ms_interval_001.jpg)  *timestamp 0.0s*

2. 接下来讲卷积核，3×3 的卷积核不是 9 个数相乘，是 27 个数相乘，因为有 3 层。

3. 然后是 padding，zero padding 让卷积后特征图尺寸保持不变。

![picture 2](src/frame_00h06m02s700ms_scene_001.jpg)  *timestamp 362.7s*
</SRT_MD>

期望输出 JSON：

```json
{{
  "blog_markdown": "## 图像数据的基本构成与卷积操作\n\n图像本质上由 RGB 三通道构成，每通道对应一个二维矩阵……（合并所有段的博客叙述）",
  "selected_timestamps": [
    {{"srt_id": 1, "timestamp": 0.0, "needs_extract": false, "source_frame_path": "src/frame_00h00m00s000ms_interval_001.jpg", "reason": "内容匹配，时间点准确"}},
    {{"srt_id": 3, "timestamp": 362.7, "needs_extract": false, "source_frame_path": "src/frame_00h06m02s700ms_scene_001.jpg", "reason": "内容匹配，时间点准确"}}
  ]
}}
```

注意上面的例子覆盖了两种状态：
- **保留**（needs_extract=false，source_frame_path 引用 SRT_MD 里出现过的图片路径）
- 4 态里的另 3 种（重截 / 删除 / 幻觉路径）下面给一个完整范例：

```json
{{
  "blog_markdown": "## ...",
  "selected_timestamps": [
    {{"srt_id": 1, "timestamp": 5.2, "needs_extract": false, "source_frame_path": "src/frame_..._interval_001.jpg", "reason": "保留：图片清晰，跟段 1 内容匹配"}},
    {{"srt_id": 2, "timestamp": 6.5, "needs_extract": true, "source_frame_path": null, "reason": "重截：原图是过渡帧，更准的时间点是 6.5s"}},
    {{"srt_id": 2, "timestamp": 7.0, "needs_extract": false, "source_frame_path": null, "reason": "删除：跟段 2 内容不相关，是过渡帧"}}
  ]
}}
```
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


def _build_srt_md_segments(
    segments: Iterable, frames: list[CandidateFrame]
) -> list[dict]:
    """Build a list of interleaved text/image segments for Stage1's
    vision call.

    Each frame is attached to the subtitle segment whose midpoint is
    closest in time, then rendered as ``{type: text, text: ...}`` /
    ``{type: image, path: ...}`` segments in document order. The
    resulting segment list, when sent via
    :func:`call_llm_async_interleaved`, lets the model pair each
    ``![picture N](path)`` markdown reference with the N-th image in
    the multimodal content array — same trick Anthropic/OpenAI vision
    docs recommend.

    Returns segments like::

        [
          {"type": "text", "text": "1. 老师讲到卷积层..."},
          {"type": "image", "path": "src/frame_..._scene_001.jpg"},
          {"type": "image", "path": "src/frame_..._interval_013.jpg"},
          {"type": "text", "text": "2. 接下来看 padding..."},
          ...
        ]
    """
    # Materialize segments into (index, text, mid_sec).
    seg_rows: list[tuple[int, str, float]] = []
    for i, seg in enumerate(segments, start=1):
        text = getattr(seg, "text", "") or ""
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start) or start)
        mid = (start + end) / 2.0
        seg_rows.append((i, text, mid))

    # Bucket frames by their nearest segment index (preserving input order).
    attached: dict[int, list[CandidateFrame]] = {i: [] for i, _, _ in seg_rows}
    for f in frames:
        if not seg_rows:
            attached.setdefault(0, []).append(f)
            continue
        nearest_i = min(
            seg_rows,
            key=lambda row: abs(row[2] - f.timestamp_sec),
        )[0]
        attached[nearest_i].append(f)

    # Build interleaved segments. Each segment line carries the picture
    # label so the model can map markdown `![picture N](path)` back to
    # the N-th image in the content array.
    out: list[dict] = []
    pic_counter = 0
    for i, text, start, end in seg_rows:
        # SRT-style timestamp header + the segment text + a blank line,
        # matching the user's preferred markdown layout.
        ts_header = _format_srt_timestamp(start, end)
        out.append({"type": "text", "text": f"{ts_header}\n{text}\n"})
        for f in attached.get(i, []):
            pic_counter += 1
            out.append({"type": "image", "path": f.path})
            out.append(
                {
                    "type": "text",
                    "text": f"![picture {pic_counter}]({f.path})",
                }
            )
    # Any frames we couldn't attach (no segments) get tacked on the end.
    for f in attached.get(0, []):
        pic_counter += 1
        out.append({"type": "image", "path": f.path})
        out.append({"type": "text", "text": f"![picture {pic_counter}]({f.path})"})
    return out


def _format_srt_timestamp(start_sec: float, end_sec: float) -> str:
    """Format a timestamp range in SRT style: ``HH:MM:SS,mmm``.

    Mirrors the format used by the cleaned subtitle.srt file so the
    Stage1 prompt reads like real SRT content.
    """
    def _fmt(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec - h * 3600 - m * 60
        s_int = int(s)
        ms = int(round((s - s_int) * 1000))
        return f"{h:02d}:{m:02d}:{s_int:02d},{ms:03d}"
    return f"{_fmt(start_sec)} --> {_fmt(end_sec)}"


def _format_picture_index(frames: list[CandidateFrame]) -> str:
    """Build a compact `{chunk_text}` placeholder body for the prompt
    template — a numbered list of every picture the model will see.

    The actual SRT_MD content (with each picture's `![](path)` reference
    sitting right next to its image in the content array) lives in the
    interleaved body segments. This index just gives the model a
    human-readable manifest at the top of the prompt.
    """
    if not frames:
        return "（无候选帧）"
    lines = ["## 候选帧清单"]
    for i, f in enumerate(frames, start=1):
        lines.append(f"- picture {i}: `{f.path}` @ {f.timestamp_sec:.1f}s")
    return "\n".join(lines)


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

        # Four valid output states per the prompt:
        #   needs_extract=true,  src=null          → 重截（ffmpeg 新截一张）
        #   needs_extract=false, src=<known path>  → 保留（用现有启发式帧）
        #   needs_extract=false, src=null          → 删除（不输出，不进 Stage2 / MD）
        #   needs_extract=false, src=<unknown>     → 删除（幻觉路径，丢掉比误截更安全）
        if needs_extract:
            src = None
            parsed.append(
                SelectedTimestamp(
                    srt_id=srt_id,
                    timestamp=timestamp,
                    needs_extract=True,
                    source_frame_path=None,
                    reason=reason,
                )
            )
        elif src in frame_paths:
            parsed.append(
                SelectedTimestamp(
                    srt_id=srt_id,
                    timestamp=timestamp,
                    needs_extract=False,
                    source_frame_path=src,
                    reason=reason,
                )
            )
        else:
            # needs_extract=False with null or unknown path → 删除
            get_reporter().record_fallback(
                "vision_stage1.frame_dropped",
                f"srt_id={srt_id} 的启发式帧被删除（path={src!r}）",
            )
            continue

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
        # Build interleaved text/image segments so the model sees each
        # `![picture N](path)` reference adjacent to its image.
        body_segments = _build_srt_md_segments(chunk.segments, frames_in_chunk)

        # The instructions + task framing go in a leading text segment,
        # with the SRT_MD content (interleaved text/image) appended.
        # We still need to fill {chunk_text} in the template — use a
        # compact reference to the picture index inside the leading
        # instructions rather than duplicating the body.
        srt_md_index = _format_picture_index(frames_in_chunk)
        instruction_text = STAGE1_PROMPT.format(
            chunk_text=srt_md_index,
            max_images=self.max_images,
        )
        all_segments = [{"type": "text", "text": instruction_text}, *body_segments]

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await call_llm_async_interleaved(
                    all_segments,
                    self.config,
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