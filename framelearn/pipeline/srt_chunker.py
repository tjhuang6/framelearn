"""Chunk SRT by video duration — each chunk covers N minutes of video.

The chunker is idempotent: passing the same SRT + same segment_minutes
always produces the same chunks.

Chunk boundaries are placed on a subtitle segment's ``start_sec`` (never
split a single segment across chunks). The last chunk may be shorter than
``segment_minutes`` — that is expected when the video duration is not an
exact multiple.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol


class HasStart(Protocol):
    """Anything with a ``start`` attribute (e.g. TranscriptSegment)."""

    start: float | None


@dataclass
class SRTChunk:
    """A contiguous slice of the video's SRT covering ~N minutes.

    Attributes:
        index: Zero-based chunk index.
        start_sec: Video timestamp where this chunk begins (seconds).
        end_sec: Video timestamp where this chunk ends (seconds).
        segments: Subtitle segments whose ``start`` lies within
            ``[start_sec, end_sec)``. Order matches the input order.
    """

    index: int
    start_sec: float
    end_sec: float
    segments: list = field(default_factory=list)


class SRTChunker:
    """Split a flat list of subtitle segments into fixed-duration chunks."""

    def __init__(self, segment_minutes: float = 10):
        """Initialize with target chunk duration.

        Args:
            segment_minutes: Target chunk size in minutes of VIDEO time
                (not subtitle count). Fractional minutes are allowed;
                defaults to 10 so a dense lecture stays close to a
                lightly polished transcript instead of being summarized.
        """
        segment_minutes = float(segment_minutes)
        if segment_minutes <= 0:
            raise ValueError(
                f"segment_minutes must be > 0, got {segment_minutes}"
            )
        self.segment_minutes = segment_minutes
        self.segment_seconds = segment_minutes * 60.0

    def chunk(self, srt_segments: Iterable) -> list[SRTChunk]:
        """Group SRT segments into chunks of ~segment_minutes each.

        Args:
            srt_segments: Iterable of objects with a ``start`` attribute
                (seconds). Segments with ``start is None`` cannot be aligned
                to a chunk boundary; they are skipped and recorded as a
                fallback warning.

        Returns:
            List of SRTChunk. Empty list when no timestamped segment remains.
        """
        all_segments = list(srt_segments)
        skipped = sum(
            1 for s in all_segments if getattr(s, "start", None) is None
        )
        if skipped:
            from framelearn.pipeline.run_report import get_reporter

            get_reporter().record_fallback(
                "srt_chunker.missing_start",
                f"{skipped} 条字幕缺少 start 时间戳，已跳过",
                detail={"skipped_count": skipped, "total_count": len(all_segments)},
            )

        segments = [
            s for s in all_segments if getattr(s, "start", None) is not None
        ]
        if not segments:
            return []

        # Sort defensively. SRT files are already in order, but if a caller
        # passes an unsorted iterable we still produce a sensible result.
        segments.sort(key=lambda s: s.start)

        chunks: list[SRTChunk] = []
        current_segments: list = []
        chunk_idx = 0
        chunk_start = segments[0].start

        for seg in segments:
            # New chunk when this segment starts past the current boundary.
            # Boundary is exclusive — a segment whose start == boundary
            # belongs to the NEW one (gives even splits on chunk-minute
            # boundaries).
            if (
                current_segments
                and seg.start - chunk_start >= self.segment_seconds
            ):
                last = current_segments[-1]
                chunks.append(
                    SRTChunk(
                        index=chunk_idx,
                        start_sec=chunk_start,
                        end_sec=(
                            float(last.end)
                            if getattr(last, "end", None) is not None
                            else float(last.start)
                        ),
                        segments=current_segments,
                    )
                )
                chunk_idx += 1
                current_segments = []
                chunk_start = seg.start
            current_segments.append(seg)

        if current_segments:
            last = current_segments[-1]
            chunks.append(
                SRTChunk(
                    index=chunk_idx,
                    start_sec=chunk_start,
                    end_sec=(
                        float(last.end)
                        if getattr(last, "end", None) is not None
                        else float(last.start)
                    ),
                    segments=current_segments,
                )
            )

        return chunks