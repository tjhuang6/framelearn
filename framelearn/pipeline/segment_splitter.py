"""Segment splitter: split subtitle + keyframes into time-aligned chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Segment:
    index: int
    start_time: float                          # 起始秒数
    end_time: float                            # 结束秒数
    subtitle: str                              # 这段的字幕文本
    keyframes: list[tuple[Path, float]] = field(default_factory=list)  # (path, timestamp)


def _parse_srt_timestamp(ts: str) -> float:
    """Convert SRT timestamp '00:03:45,123' to seconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    return h * 3600 + m * 60 + s


def _split_by_srt(
    srt_text: str,
    keyframes: list[tuple[Path, float]],
    segment_duration: float,
    max_keyframes_per_segment: int,
) -> list[Segment]:
    """Split using SRT timestamps for precise alignment."""

    # Parse SRT into (start, end, text) entries
    entries: list[tuple[float, float, str]] = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())

    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        # Find timestamp line
        ts_match = None
        ts_line_idx = 0
        for i, line in enumerate(lines):
            m = re.match(
                r"(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{3})",
                line,
            )
            if m:
                ts_match = m
                ts_line_idx = i
                break
        if not ts_match:
            continue

        start = _parse_srt_timestamp(ts_match.group(1))
        end = _parse_srt_timestamp(ts_match.group(2))
        text_lines = [l for l in lines[ts_line_idx + 1:] if not l.isdigit()]
        text = " ".join(text_lines)
        if text:
            entries.append((start, end, text))

    if not entries:
        return []

    # Group entries into segments of ~segment_duration seconds
    segments: list[Segment] = []
    current_start = entries[0][0]
    current_texts: list[str] = []
    seg_index = 0

    for start, end, text in entries:
        current_texts.append(text)
        if end - current_start >= segment_duration:
            seg = Segment(
                index=seg_index,
                start_time=current_start,
                end_time=end,
                subtitle="\n".join(current_texts),
            )
            segments.append(seg)
            seg_index += 1
            current_start = end
            current_texts = []

    # Last segment
    if current_texts:
        last_end = entries[-1][1]
        segments.append(Segment(
            index=seg_index,
            start_time=current_start,
            end_time=last_end,
            subtitle="\n".join(current_texts),
        ))

    # Assign keyframes to segments
    for seg in segments:
        seg.keyframes = [
            (path, ts)
            for path, ts in keyframes
            if seg.start_time <= ts <= seg.end_time
        ][:max_keyframes_per_segment]

    return segments


def _split_by_chars(
    text: str,
    keyframes: list[tuple[Path, float]],
    segment_duration: float,
    max_keyframes_per_segment: int,
    chars_per_second: float = 4.0,
) -> list[Segment]:
    """Fallback: split plain text by estimated time (no SRT timestamps)."""

    total_chars = len(text)
    chars_per_segment = int(segment_duration * chars_per_second)

    segments: list[Segment] = []
    offset = 0
    seg_index = 0

    while offset < total_chars:
        chunk = text[offset: offset + chars_per_segment]
        start_time = (offset / chars_per_second)
        end_time = ((offset + len(chunk)) / chars_per_second)

        seg = Segment(
            index=seg_index,
            start_time=start_time,
            end_time=end_time,
            subtitle=chunk.strip(),
        )
        seg.keyframes = [
            (path, ts)
            for path, ts in keyframes
            if start_time <= ts <= end_time
        ][:max_keyframes_per_segment]

        segments.append(seg)
        offset += chars_per_segment
        seg_index += 1

    return segments


def split_segments(
    subtitle: str,
    keyframes: list[tuple[Path, float]],
    segment_duration: float = 90.0,
    max_keyframes_per_segment: int = 10,
    srt_text: str | None = None,
) -> list[Segment]:
    """Split subtitle and keyframes into time-aligned segments.

    Args:
        subtitle: Cleaned plain-text subtitle
        keyframes: List of (frame_path, timestamp_seconds) tuples
        segment_duration: Target duration per segment in seconds
        max_keyframes_per_segment: Max keyframes to include per segment
        srt_text: Raw SRT content (used for precise time splitting if available)

    Returns:
        List of Segment objects, each with subtitle text and keyframes
    """
    if srt_text:
        segments = _split_by_srt(
            srt_text, keyframes, segment_duration, max_keyframes_per_segment
        )
        if segments:
            return segments

    # Fallback to char-based splitting
    return _split_by_chars(
        subtitle, keyframes, segment_duration, max_keyframes_per_segment
    )
