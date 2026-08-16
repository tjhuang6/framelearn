"""Parse SRT/VTT subtitle files into :class:`TranscriptSegment` records.

The old ``--subtitle`` path stripped timestamps to plain text and then
synthesised fake 5-second intervals, which destroyed the timing
information the chunker and frame distributor depend on. This module is
the single place that understands SRT/VTT timestamps.
"""

from __future__ import annotations

import re
from pathlib import Path

from framelearn.pipeline.asr_adapter import TranscriptSegment


_SRT_TS_RE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})"
)
_VTT_TS_RE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}:\d{2}\.\d{3}|"
    r"\d{1,2}:\d{2}\.\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{1,2}:\d{2}\.\d{3})"
)


def _parse_timestamp(ts: str) -> float:
    """Convert an SRT/VTT timestamp to seconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
    else:
        h, m, s = "0", parts[0], parts[1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_srt_segments(text: str) -> list[TranscriptSegment]:
    """Parse SRT content into segments with ``start``/``end`` in seconds.

    Skips cue indices, WEBVTT headers and malformed blocks. Text is the
    joined content of all non-timestamp lines in each cue.
    """
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\n\s*\n", text.strip())

    for block in blocks:
        lines = [line.strip() for line in block.splitlines()]
        lines = [line for line in lines if line and line.upper() != "WEBVTT"]

        ts_match = None
        ts_line_idx = 0
        for i, line in enumerate(lines):
            match = _SRT_TS_RE.match(line) or _VTT_TS_RE.match(line)
            if match:
                ts_match = match
                ts_line_idx = i
                break
        if not ts_match:
            continue

        text_lines = [
            line
            for line in lines[ts_line_idx + 1 :]
            if not line.isdigit()
        ]
        text = " ".join(text_lines).strip()
        if not text:
            continue

        start = _parse_timestamp(ts_match.group("start"))
        end = _parse_timestamp(ts_match.group("end"))
        if end <= start:
            continue
        segments.append(TranscriptSegment(text=text, start=start, end=end))

    segments.sort(key=lambda seg: (seg.start or 0.0, seg.end or 0.0))
    return segments


def parse_subtitle_file(path: Path) -> tuple[list[TranscriptSegment], str]:
    """Parse a subtitle file on disk.

    Returns ``(segments, full_text)``. ``.txt`` files have no timing
    information, so ``segments`` is empty and the caller decides how to
    synthesize timestamps.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() not in (".srt", ".vtt"):
        return [], text

    segments = parse_srt_segments(text)
    full_text = "\n".join(seg.text for seg in segments)
    return segments, full_text
