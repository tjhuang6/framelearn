"""Focused tests for the anchored blog pipeline helpers."""

from pathlib import Path

from framelearn.pipeline.asr_adapter import TranscriptSegment
from framelearn.pipeline.blog_generator import BlogGeneratorOutput, FrameRequest
from framelearn.pipeline.chunked_doc_generator import (
    ChunkedDocGenerator,
    _copy_kept_frame_to_src,
    _globalize_chunk_anchors,
)
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.srt_chunker import SRTChunk


def _write_jpg(path: Path):
    path.write_bytes(b"\xff\xd8\xff\xd9")


def _chunk():
    return SRTChunk(
        index=0,
        start_sec=0.0,
        end_sec=10.0,
        segments=[
            TranscriptSegment(text="第一条字幕", start=0.0, end=5.0),
            TranscriptSegment(text="第二条字幕", start=5.0, end=10.0),
        ],
    )


def test_copy_kept_frame_avoids_name_collisions(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    chunk_dir = tmp_path / "chunk_0"
    chunk_dir.mkdir()

    frame = chunk_dir / "extra_frame_000.jpg"
    _write_jpg(frame)

    first = _copy_kept_frame_to_src(frame, src)
    assert first == src / "extra_frame_000.jpg"

    other = tmp_path / "chunk_1" / "extra_frame_000.jpg"
    other.parent.mkdir()
    _write_jpg(other)
    second = _copy_kept_frame_to_src(other, src)
    assert second.name == "extra_frame_000_2.jpg"
    assert second.exists()


def test_globalize_chunk_anchors_prefixes_anchor_ids():
    output = BlogGeneratorOutput(
        blog_markdown="正文 [[FRAME:a1@53.0]] 结束",
        frame_requests=[
            FrameRequest(
                anchor_id="a1",
                srt_id=1,
                timestamp=53.0,
                request_type="new_capture",
                source_frame_path=None,
                reason="x",
            )
        ],
    )
    globalized = _globalize_chunk_anchors(3, output)
    assert "[[FRAME:c3_a1@53.0]]" in globalized.blog_markdown
    assert globalized.frame_requests[0].anchor_id == "c3_a1"


def test_resolve_reuse_anchor_binds_real_frame(tmp_path):
    frame = tmp_path / "candidate.jpg"
    _write_jpg(frame)
    frames = [CandidateFrame(path=str(frame), timestamp_sec=53.0, source="heuristic")]
    request = FrameRequest(
        anchor_id="c0_a1",
        srt_id=1,
        timestamp=53.0,
        request_type="reuse",
        source_frame_path=str(frame),
        reason="x",
    )
    items = ChunkedDocGenerator()._resolve_chunk_anchors(
        _chunk(), [request], frames, video_path="unused.mp4", temp_frames=tmp_path, local_offset=1
    )
    assert len(items) == 1
    assert items[0].frame_path == str(frame)
    assert items[0].timestamp == 53.0


def test_resolve_invalid_reuse_anchor_is_dropped(tmp_path):
    request = FrameRequest(
        anchor_id="c0_a1",
        srt_id=1,
        timestamp=53.0,
        request_type="reuse",
        source_frame_path="missing.jpg",
        reason="x",
    )
    items = ChunkedDocGenerator()._resolve_chunk_anchors(
        _chunk(), [request], [], video_path="unused.mp4", temp_frames=tmp_path, local_offset=1
    )
    assert items == []


def test_resolve_new_capture_matches_within_tolerance(tmp_path):
    frame = tmp_path / "candidate.jpg"
    _write_jpg(frame)
    frames = [CandidateFrame(path=str(frame), timestamp_sec=53.5, source="heuristic")]
    request = FrameRequest(
        anchor_id="c0_a1",
        srt_id=2,
        timestamp=54.0,
        request_type="new_capture",
        source_frame_path=None,
        reason="x",
    )
    items = ChunkedDocGenerator()._resolve_chunk_anchors(
        _chunk(), [request], frames, video_path="unused.mp4", temp_frames=tmp_path, local_offset=1
    )
    assert len(items) == 1
    assert items[0].frame_path == str(frame)
    assert items[0].timestamp == 53.5


def test_resolve_new_capture_outside_tolerance_calls_ffmpeg(tmp_path, monkeypatch):
    request = FrameRequest(
        anchor_id="c0_a1",
        srt_id=1,
        timestamp=54.0,
        request_type="new_capture",
        source_frame_path=None,
        reason="x",
    )

    captured = []

    def fake_capture(video, timestamp, output):
        captured.append((timestamp, output))
        Path(output).write_bytes(b"\xff\xd8\xff\xd9")
        return True

    monkeypatch.setattr(
        "framelearn.pipeline.ffmpeg_helper.FFmpegHelper.capture_single_frame",
        staticmethod(fake_capture),
    )
    items = ChunkedDocGenerator()._resolve_chunk_anchors(
        _chunk(), [request], [], video_path="unused.mp4", temp_frames=tmp_path, local_offset=1
    )
    assert captured and captured[0][0] == 54.0
    assert len(items) == 1
    assert items[0].timestamp == 54.0
    assert Path(items[0].frame_path).exists()
