"""Regression tests for issues found in the 2026-08-16 code review."""

from pathlib import Path

from framelearn.pipeline.asr_adapter import TranscriptSegment
from framelearn.pipeline.frame_distributor import FrameDistributor, _evenly_subsample
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.srt_chunker import SRTChunker
from framelearn.pipeline.srt_parser import parse_srt_segments, parse_subtitle_file
from framelearn.pipeline.text_cleaner import _parse_llm_response, _strip_fillers_locally
from framelearn.pipeline.vision_stage1 import _parse_stage1
from framelearn.pipeline.vision_stage2 import _parse_stage2


SRT = """1
00:00:00,000 --> 00:00:02,500
大家好

2
00:00:02,500 --> 00:00:05,000
今天讲 Python
"""


def test_parse_srt_segments_preserves_timestamps():
    segments = parse_srt_segments(SRT)
    assert len(segments) == 2
    assert segments[0].text == "大家好"
    assert segments[0].start == 0.0
    assert segments[0].end == 2.5
    assert segments[1].start == 2.5
    assert segments[1].end == 5.0


def test_parse_subtitle_file_srt(tmp_path):
    path = tmp_path / "subtitle.srt"
    path.write_text(SRT, encoding="utf-8")
    segments, full_text = parse_subtitle_file(path)
    assert len(segments) == 2
    assert "大家好" in full_text


def test_srt_chunker_skips_missing_start_and_returns_empty():
    segment = TranscriptSegment(text="无时间戳", start=None, end=None)
    assert SRTChunker(30).chunk([segment]) == []


def test_frame_distributor_max_one_does_not_divide_by_zero():
    chunks = SRTChunker(30).chunk(
        [
            TranscriptSegment(text="a", start=0.0, end=1.0),
            TranscriptSegment(text="b", start=1.0, end=2.0),
        ]
    )
    frames = [
        CandidateFrame(path="a.jpg", timestamp_sec=0.5, source="heuristic"),
        CandidateFrame(path="b.jpg", timestamp_sec=1.5, source="heuristic"),
    ]
    buckets = FrameDistributor(max_per_chunk=1).distribute(chunks, frames)
    assert len(buckets[0]) == 1


def test_evenly_subsample_k_one_returns_first_item():
    items = [
        CandidateFrame(path=f"{i}.jpg", timestamp_sec=float(i), source="heuristic")
        for i in range(3)
    ]
    assert _evenly_subsample(items, 1) == [items[0]]


def test_text_cleaner_validates_id_order():
    raw = '{"segments": [{"id": 2, "text": "b"}, {"id": 1, "text": "a"}]}'
    assert _parse_llm_response(raw, expected_count=2) is None

    raw = '{"segments": [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]}'
    assert _parse_llm_response(raw, expected_count=2) == [
        {"id": 1, "text": "a"},
        {"id": 2, "text": "b"},
    ]


def test_filler_stripping_handles_empty_list():
    assert _strip_fillers_locally(" 那么 大家好 ", []) == "那么 大家好"


def test_stage1_string_false_is_treated_as_delete():
    """``bool("false")`` must never turn a delete into an extraction."""
    raw = (
        '{"blog_markdown": "ok", "selected_timestamps": ['
        '{"srt_id": 1, "timestamp": 1.0, "needs_extract": "false", '
        '"source_frame_path": null, "reason": "x"}]}'
    )
    frames = [CandidateFrame(path="a.jpg", timestamp_sec=1.0, source="heuristic")]
    out = _parse_stage1(raw, frames)
    assert out is not None
    assert out.selected_timestamps == []


def test_stage2_string_false_is_treated_as_discard():
    raw = '{"decisions": [{"frame": "a.jpg", "keep": "false", "reason": "x"}]}'
    frames = [CandidateFrame(path="a.jpg", timestamp_sec=1.0, source="heuristic")]
    decisions = _parse_stage2(raw, frames, [1])
    assert decisions is not None
    assert decisions[0].keep is False
