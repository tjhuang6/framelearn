"""Assemble the two final Markdown files for the anchored blog pipeline.

Two outputs:

* ``srt_picture.md`` — SRT preserved as blockquotes with HH:MM:SS
  timestamps; kept frame anchors render as images under the matching
  subtitle segment.
* ``blog.md`` — per-chunk blog markdown concatenated with
  ``[[FRAME:...]]`` anchors replaced by their validated images.

Both filenames come from settings.toml ``[doc_gen] srt_filename`` /
``blog_filename``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from framelearn.config import get as config_get
from framelearn.pipeline.vision_frame_evaluator import FrameEvaluation

ANCHOR_RE = re.compile(
    r"\[\[FRAME:(?P<anchor_id>[A-Za-z0-9_-]+)@"
    r"(?P<timestamp>\d+(?:\.\d+)?)\]\]"
)


def format_hms(seconds: float) -> str:
    """Format ``seconds`` as ``HH:MM:SS`` (drop sub-second precision)."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _segment_iter_for_display(
    segments: Iterable,
) -> list[tuple[int, float, float, str]]:
    """Build (display_index, start_sec, end_sec, text) for output.

    ``display_index`` is 1-based and matches the ``srt_id`` stored on
    :class:`FrameEvaluation` by the anchored pipeline.
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

    def _render_kept_frame(
        self,
        evaluation: FrameEvaluation,
        image_prefix: str = "src/",
    ) -> str:
        """Render a kept frame and its optional caption/text content."""
        filename = Path(evaluation.frame_path).name
        lines = [f"![图]({image_prefix}{filename})"]
        if evaluation.caption.strip():
            lines.append("")
            lines.append(f"*{evaluation.caption.strip()}*")
        if evaluation.text_representation.strip():
            lines.append("")
            lines.append(evaluation.text_representation.strip())
        return "\n".join(lines)

    def assemble_blog_anchored(
        self,
        all_blog_markdowns: list[str],
        evaluations_by_anchor: dict[str, FrameEvaluation],
        video_title: str = "视频讲义（博客版）",
        image_prefix: str = "src/",
    ) -> str:
        """Assemble ``blog.md`` by replacing ``[[FRAME:...]]`` anchors.

        Kept anchors become image Markdown; discarded or unknown anchors are
        removed from the text entirely.
        """
        def replace_anchor(match):
            evaluation = evaluations_by_anchor.get(match.group("anchor_id"))
            if evaluation is None or not evaluation.keep_image:
                return ""
            return self._render_kept_frame(evaluation, image_prefix)

        lines = [f"# {video_title}", ""]
        for chunk_md in all_blog_markdowns:
            rendered = ANCHOR_RE.sub(replace_anchor, chunk_md.strip())
            rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
            lines.append(rendered)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def assemble_srt_anchored(
        self,
        srt_segments: Iterable,
        evaluations_by_anchor: dict[str, FrameEvaluation],
        video_title: str = "视频讲义",
        image_prefix: str = "src/",
    ) -> str:
        """Assemble ``srt_picture.md`` from raw SRT order + kept anchors."""
        rows = _segment_iter_for_display(srt_segments)
        images_by_srt: dict[int, list[FrameEvaluation]] = defaultdict(list)
        for evaluation in evaluations_by_anchor.values():
            if evaluation.keep_image:
                images_by_srt[evaluation.srt_id].append(evaluation)

        lines = [f"# {video_title}", ""]
        for srt_id, start, end, text in rows:
            lines.append(
                f"> {srt_id}. **{format_hms(start)} - {format_hms(end)}**  "
            )
            lines.append(f"> {text}")
            lines.append("")
            for evaluation in images_by_srt.get(srt_id, []):
                lines.append(self._render_kept_frame(evaluation, image_prefix))
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def write_anchored(
        self,
        output_dir: Path,
        srt_segments: Iterable,
        all_blog_markdowns: list[str],
        evaluations_by_anchor: dict[str, FrameEvaluation],
        video_title: str = "视频讲义",
    ) -> tuple[Path, Path]:
        """Write both outputs for the anchored blog pipeline."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        srt_content = self.assemble_srt_anchored(
            srt_segments, evaluations_by_anchor, video_title=video_title
        )
        blog_content = self.assemble_blog_anchored(
            all_blog_markdowns,
            evaluations_by_anchor,
            video_title=video_title,
        )

        from framelearn.file_utils import atomic_write_text

        srt_path = output_dir / self.srt_filename
        blog_path = output_dir / self.blog_filename
        atomic_write_text(srt_path, srt_content)
        atomic_write_text(blog_path, blog_content)
        return srt_path, blog_path
