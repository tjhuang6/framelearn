"""BlogGenerator — text model generates blog prose plus frame anchors.

The text model receives an annotated SRT chunk: raw subtitle segments with
``![picture N @ ts](path)`` markers inserted after the nearest segment.
It outputs ``blog_markdown`` containing ``[[FRAME:<anchor_id>@<timestamp>]]``
anchors and an explicit ``frame_requests`` list. Program code later binds
those anchors to real frames.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass

from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.llm_json import parse_json_object
from framelearn.pipeline.run_report import get_reporter
from framelearn.pipeline.srt_chunker import SRTChunk
from framelearn.provider_adapter import (
    ProviderConfig,
    call_llm_async,
    load_text_config,
)


ANCHOR_RE = re.compile(
    r"\[\[FRAME:(?P<anchor_id>[A-Za-z0-9_-]+)@"
    r"(?P<timestamp>\d+(?:\.\d+)?)\]\]"
)

BLOG_GENERATOR_PROMPT = """你是资深教育内容编辑。你会收到一段带候选截图标记的字幕（annotated SRT chunk）。

## 任务

1. 把字幕整理成一篇连贯的博客式笔记（blog_markdown）。
2. 在博客中你希望配图的位置，插入锚点：[[FRAME:<anchor_id>@<timestamp>]]。
   - 如果你复用某个候选截图，anchor_id 可以写成 a1、a2...，timestamp 必须使用该候选截图的真实时间戳。
   - 如果你需要一张候选截图没有覆盖的新截图，也写 [[FRAME:<anchor_id>@<timestamp>]]，timestamp 使用你希望截取的精准秒数。
3. 在 frame_requests 中列出每个锚点的详情。

## 博客风格要求

- 去除口语词，不写“主讲人说 / 他说”等元引用。
- 完整保留知识点、逻辑链、例子和强调。
- 用连贯段落，不堆砌列表。
- 不添加字幕中没有的知识。

## 候选截图标记说明

输入中 `![picture N @ 53.0s](路径)` 表示程序在该视频时间附近有一张候选截图。
你无法看到图片内容，只能看到时间戳与路径。因此：

- 复用候选截图时：request_type="reuse"，source_frame_path 必须填写输入中真实出现过的路径。
- 认为候选时间不够准确时：request_type="new_capture"，source_frame_path=null，timestamp 写精准秒数。
- 无法判断内容是否合适是正常的，后续视觉模型会负责判断图片质量。

## 输出 JSON（严格格式，不要解释）

{{
  "blog_markdown": "## 标题\\n\\n正文...[[FRAME:a1@53.0]]...",
  "frame_requests": [
    {{
      "anchor_id": "a1",
      "srt_id": 3,
      "timestamp": 53.0,
      "request_type": "reuse",
      "source_frame_path": "src/frame_00h00m53s000ms_interval_005.jpg",
      "reason": "这里讲解卷积操作"
    }},
    {{
      "anchor_id": "a2",
      "srt_id": 5,
      "timestamp": 633.8,
      "request_type": "new_capture",
      "source_frame_path": null,
      "reason": "这里需要一张代码截图"
    }}
  ]
}}

约束：
- anchor_id 必须唯一，格式为 a1、a2、a3...
- blog_markdown 中出现的每个 [[FRAME:id@timestamp]] 都必须在 frame_requests 中
- blog_markdown 中的 anchor_id 和 timestamp 必须与 frame_requests 完全一致
- srt_id 使用输入字幕段的 1-based 序号
- 不要输出 markdown 之外的解释文字

## 输入

<SRT_MD>
{chunk_text}
</SRT_MD>
"""


@dataclass
class FrameRequest:
    anchor_id: str
    srt_id: int
    timestamp: float
    request_type: str  # "reuse" | "new_capture"
    source_frame_path: str | None
    reason: str


@dataclass
class BlogGeneratorOutput:
    blog_markdown: str
    frame_requests: list[FrameRequest]
    degraded: bool = False


def _format_srt_timestamp(start_sec: float, end_sec: float) -> str:
    """Format ``seconds`` as an SRT timestamp range."""
    def _fmt(sec: float) -> str:
        total_ms = max(0, round(float(sec) * 1000))
        h, rem = divmod(total_ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    return f"{_fmt(start_sec)} --> {_fmt(end_sec)}"


def _attach_frames_to_segments(
    segments: list, frames: list[CandidateFrame]
) -> dict[int, list[CandidateFrame]]:
    """Attach each frame to the segment whose midpoint is nearest.

    Returns ``{segment_index_1_based: [frames in timestamp order]}``.
    """
    if not segments:
        return {}

    rows = []
    for i, seg in enumerate(segments, start=1):
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start) or start)
        rows.append((i, start, end))

    attached: dict[int, list[CandidateFrame]] = {i: [] for i, _, _ in rows}
    for frame in sorted(frames, key=lambda f: f.timestamp_sec):
        nearest = min(
            rows,
            key=lambda row: abs((row[1] + row[2]) / 2.0 - frame.timestamp_sec),
        )[0]
        attached[nearest].append(frame)
    return attached


def build_annotated_srt(chunk: SRTChunk, frames: list[CandidateFrame]) -> str:
    """Build the annotated SRT chunk text sent to BlogGenerator.

    Uses design option A: the chunk already exists; candidate frames are
    inserted after the nearest subtitle segment without modifying raw SRT.
    """
    attached = _attach_frames_to_segments(chunk.segments, frames)
    lines: list[str] = []
    picture_index = 0
    for i, seg in enumerate(chunk.segments, start=1):
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start) or start)
        text = getattr(seg, "text", "") or ""
        lines.append(_format_srt_timestamp(start, end))
        lines.append(text.strip())
        lines.append("")
        for frame in attached.get(i, []):
            picture_index += 1
            lines.append(
                f"![picture {picture_index} @ {frame.timestamp_sec:.1f}s]"
                f"({frame.path})"
            )
            lines.append("")
    return "\n".join(lines).strip()


def _parse_frame_requests(data: dict) -> list[FrameRequest] | None:
    """Parse and validate ``frame_requests``.

    Returns None when the schema is invalid; the caller retries.
    """
    raw_requests = data.get("frame_requests")
    if not isinstance(raw_requests, list):
        return None

    requests: list[FrameRequest] = []
    seen_anchors: set[str] = set()
    for item in raw_requests:
        if not isinstance(item, dict):
            return None
        anchor_id = item.get("anchor_id")
        if not isinstance(anchor_id, str) or not anchor_id:
            return None
        if anchor_id in seen_anchors:
            return None
        seen_anchors.add(anchor_id)

        try:
            srt_id = int(item["srt_id"])
            timestamp = float(item["timestamp"])
        except (KeyError, TypeError, ValueError):
            return None
        if srt_id < 1 or not math.isfinite(timestamp) or timestamp < 0:
            return None

        request_type = item.get("request_type")
        if request_type not in ("reuse", "new_capture"):
            return None

        src = item.get("source_frame_path")
        if request_type == "reuse":
            if not isinstance(src, str) or not src:
                return None
        elif src is not None:
            return None

        reason = str(item.get("reason", ""))
        requests.append(
            FrameRequest(
                anchor_id=anchor_id,
                srt_id=srt_id,
                timestamp=timestamp,
                request_type=request_type,
                source_frame_path=src,
                reason=reason,
            )
        )
    return requests


def _parse_blog_output(
    raw: str,
    chunk: SRTChunk,
    frames: list[CandidateFrame],
) -> BlogGeneratorOutput | None:
    """Parse and validate the text model response."""
    data = parse_json_object(raw)
    if data is None:
        return None

    blog = data.get("blog_markdown")
    if not isinstance(blog, str) or not blog.strip():
        return None

    requests = _parse_frame_requests(data)
    if requests is None:
        return None

    markers = [
        (m.group("anchor_id"), float(m.group("timestamp")))
        for m in ANCHOR_RE.finditer(blog)
    ]
    request_map = {r.anchor_id: r for r in requests}

    for anchor_id, timestamp in markers:
        request = request_map.get(anchor_id)
        if request is None or abs(request.timestamp - timestamp) > 1e-6:
            return None
        if request.srt_id < 1 or request.srt_id > len(chunk.segments):
            return None
        if request.request_type == "reuse":
            valid_paths = {f.path for f in frames}
            if request.source_frame_path not in valid_paths:
                return None

    # Every request should appear in the markdown. If a request is missing,
    # downstream would leave it unused; treat that as a schema error.
    if set(request_map) != {anchor_id for anchor_id, _ in markers}:
        return None

    return BlogGeneratorOutput(blog_markdown=blog, frame_requests=requests)


def fallback_blog(chunk: SRTChunk) -> BlogGeneratorOutput:
    """Fallback when the text model keeps failing."""
    blog = "\n\n".join(
        str(getattr(seg, "text", "")).strip()
        for seg in chunk.segments
        if str(getattr(seg, "text", "")).strip()
    )
    return BlogGeneratorOutput(blog_markdown=blog, frame_requests=[], degraded=True)


class BlogGenerator:
    """Call the text model with an annotated SRT chunk."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        max_retries: int = 2,
        timeout: int = 300,
    ):
        self.config = config or load_text_config()
        self.max_retries = max_retries
        self.timeout = timeout

    async def generate(
        self,
        chunk: SRTChunk,
        frames: list[CandidateFrame],
    ) -> BlogGeneratorOutput:
        """Generate blog markdown and frame requests for one chunk."""
        chunk_text = build_annotated_srt(chunk, frames)
        prompt = BLOG_GENERATOR_PROMPT.format(chunk_text=chunk_text)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await call_llm_async(
                    prompt,
                    self.config,
                    max_tokens=8192,
                    timeout=self.timeout,
                )
                parsed = _parse_blog_output(response, chunk, frames)
                if parsed is None:
                    raise ValueError("BlogGenerator response did not match schema")
                return parsed
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

        get_reporter().record_fallback(
            "blog_generator.fallback",
            f"chunk {chunk.index} BlogGenerator 失败（{last_error}），降级为原始字幕拼接",
        )
        return fallback_blog(chunk)
