"""Assemble the two final Markdown files from chunked outputs.

Two outputs:

* ``srt_picture.md`` — cleaned SRT preserved as a sequence of blockquotes
  with HH:MM:SS timestamps; an ``![]()`` image reference inserted after
  the segment whose ``srt_id`` matches a kept :class:`FrameDecision`.
* ``blog.md`` — per-chunk blog markdown concatenated with the same image
  references inserted after the matching SRT segment's prose paragraph.

Both filenames come from settings.toml ``[doc_gen] srt_filename`` /
``blog_filename``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from framelearn.config import get as config_get
from framelearn.pipeline.vision_stage2 import FrameDecision


def format_hms(seconds: float) -> str:
    """Format ``seconds`` as ``HH:MM:SS`` (drop sub-second precision)."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _segment_iter_for_display(segments: Iterable) -> list[tuple[int, float, float, str]]:
    """Build (display_index, start_sec, end_sec, text) for one-segment-per-line output.

    ``display_index`` is 1-based and matches what Stage1 / Stage2 used as
    ``srt_id`` when frames were associated with these segments.
    """
    rows = []
    for i, seg in enumerate(segments, start=1):
        start = getattr(seg, "start", None) or 0.0
        end = getattr(seg, "end", None) or start
        text = getattr(seg, "text", "") or ""
        rows.append((i, start, end, text))
    return rows


@dataclass
class MDAssembler:
    """Assemble the two final Markdown files.

    File names are read from settings.toml ``[doc_gen]`` and can be
    overridden in the constructor (handy for tests).
    """

    srt_filename: str
    blog_filename: str

    def __init__(
        self,
        srt_filename: str | None = None,
        blog_filename: str | None = None,
    ):
        self.srt_filename = (
            srt_filename
            if srt_filename is not None
            else str(config_get("doc_gen.srt_filename", "srt_picture.md"))
        )
        self.blog_filename = (
            blog_filename
            if blog_filename is not None
            else str(config_get("doc_gen.blog_filename", "blog.md"))
        )

    def assemble_srt(
        self,
        cleaned_srt_segments: Iterable,
        all_decisions: list[FrameDecision],
        video_title: str = "视频讲义",
        image_prefix: str = "src/",
    ) -> str:
        """Build ``srt_picture.md`` content.

        Args:
            cleaned_srt_segments: Iterable of segments with
                ``start``/``end``/``text`` (TranscriptSegment-compatible).
            all_decisions: Every FrameDecision across all chunks; only
                those with ``keep=True`` insert images.
            video_title: H1 title for the document.
            image_prefix: Path prefix added to image src attributes.
        """
        rows = _segment_iter_for_display(cleaned_srt_segments)
        # Group kept decisions by srt_id so multiple images can attach to
        # the same segment.
        images_by_srt: dict[int, list[str]] = defaultdict(list)
        for d in all_decisions:
            if d.keep:
                images_by_srt[d.srt_id].append(d.frame_path)

        lines = [f"# {video_title}", ""]
        for srt_id, start, end, text in rows:
            lines.append(f"> {srt_id}. **{format_hms(start)} - {format_hms(end)}**  ")
            lines.append(f"> {text}")
            lines.append("")
            for path in images_by_srt.get(srt_id, []):
                # Convert absolute path to a path relative to the output
                # directory. Caller is responsible for copying the JPEGs
                # into a known location (typically ``<output>/src/``).
                filename = Path(path).name
                lines.append(f"![图]({image_prefix}{filename})")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def assemble_blog(
        self,
        all_blog_markdowns: list[str],
        all_decisions: list[FrameDecision],
        video_title: str = "视频讲义（博客版）",
        image_prefix: str = "src/",
        chunk_ranges: list[tuple[int, int]] | None = None,
    ) -> str:
        """Build ``blog.md`` content.

        Each chunk's blog markdown is rendered as a section. When
        ``chunk_ranges`` is supplied (one ``(first_srt_id, last_srt_id)``
        tuple per blog section), kept images are inserted at the end of the
        section they belong to. Without ranges, the old all-images-at-end
        layout is used as a fallback.
        """
        images_by_srt: dict[int, list[str]] = defaultdict(list)
        for d in all_decisions:
            if d.keep:
                images_by_srt[d.srt_id].append(d.frame_path)

        has_ranges = (
            chunk_ranges is not None
            and len(chunk_ranges) == len(all_blog_markdowns)
        )

        lines = [f"# {video_title}", ""]
        for index, chunk_md in enumerate(all_blog_markdowns):
            lines.append(chunk_md.strip())
            lines.append("")

            if not has_ranges:
                continue
            start, end = chunk_ranges[index]
            chunk_images: list[str] = []
            for srt_id in range(start, end + 1):
                for path in images_by_srt.get(srt_id, []):
                    chunk_images.append(path)
            if not chunk_images:
                continue
            lines.append("**本段配图**")
            lines.append("")
            for path in chunk_images:
                filename = Path(path).name
                lines.append(f"![图]({image_prefix}{filename})")
                lines.append("")

        if not has_ranges:
            # Fallback: keep the previous behaviour (all images at the end).
            all_paths: list[tuple[int, str]] = []
            for srt_id, paths in images_by_srt.items():
                for p in paths:
                    all_paths.append((srt_id, p))
            all_paths.sort(key=lambda x: x[0])
            if all_paths:
                lines.append("---")
                lines.append("")
                lines.append("## 配图")
                lines.append("")
                for _, path in all_paths:
                    filename = Path(path).name
                    lines.append(f"![图]({image_prefix}{filename})")
                    lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def write(
        self,
        output_dir: Path,
        srt_segments: Iterable,
        all_blog_markdowns: list[str],
        all_decisions: list[FrameDecision],
        video_title: str = "视频讲义",
        chunk_ranges: list[tuple[int, int]] | None = None,
    ) -> tuple[Path, Path]:
        """Write both files to ``output_dir``. Returns their paths."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        srt_content = self.assemble_srt(
            srt_segments, all_decisions, video_title=video_title
        )
        blog_content = self.assemble_blog(
            all_blog_markdowns,
            all_decisions,
            video_title=video_title,
            chunk_ranges=chunk_ranges,
        )

        srt_path = output_dir / self.srt_filename
        blog_path = output_dir / self.blog_filename
        srt_path.write_text(srt_content, encoding="utf-8")
        blog_path.write_text(blog_content, encoding="utf-8")
        return srt_path, blog_path