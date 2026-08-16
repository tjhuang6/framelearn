"""Focused tests for the chunked document generator fixes."""

import asyncio
from pathlib import Path

from framelearn.pipeline.asr_adapter import TranscriptSegment
from framelearn.pipeline.chunked_doc_generator import (
    ChunkedDocGenerator,
    _copy_kept_frame_to_src,
)
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.srt_chunker import SRTChunk
from framelearn.pipeline.vision_stage1 import SelectedTimestamp, VisionStage1Output
from framelearn.pipeline.vision_stage2 import FrameDecision


def _write_jpg(path: Path):
    path.write_bytes(b"\xff\xd8\xff\xd9")


def test_copy_kept_frame_avoids_name_collisions(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    chunk_dir = tmp_path / "chunk_0"
    chunk_dir.mkdir()

    frame = chunk_dir / "extra_frame_000.jpg"
    _write_jpg(frame)

    first = _copy_kept_frame_to_src(frame, src)
    assert first == src / "extra_frame_000.jpg"

    # Simulate the same filename from another chunk.
    other = tmp_path / "chunk_1" / "extra_frame_000.jpg"
    other.parent.mkdir()
    _write_jpg(other)
    second = _copy_kept_frame_to_src(other, src)
    assert second.name == "extra_frame_000_2.jpg"
    assert second.exists()


def test_process_chunk_applies_stage1_adjusted_timestamp(tmp_path, monkeypatch):
    """Stage1's ±2s timestamp adjustment must reach Stage2."""
    import framelearn.pipeline.chunked_doc_generator as module

    frame = tmp_path / "frame_00h00m05s000ms_scene_001.jpg"
    _write_jpg(frame)
    heuristic = [
        CandidateFrame(path=str(frame), timestamp_sec=5.0, source="heuristic")
    ]

    s1_out = VisionStage1Output(
        blog_markdown="blog",
        selected_timestamps=[
            SelectedTimestamp(
                srt_id=1,
                timestamp=5.7,
                needs_extract=False,
                source_frame_path=str(frame),
                reason="adjust +0.7s",
            )
        ],
    )

    class FakeStage1:
        def __init__(self, max_images=None):
            pass

        async def process(self, chunk, frames):
            return s1_out

    captured = {}

    class FakeStage2:
        async def process(self, chunk, frames, srt_id_per_frame):
            captured["frames"] = list(frames)
            captured["srt_ids"] = list(srt_id_per_frame)
            return [
                FrameDecision(
                    srt_id=srt_id_per_frame[0],
                    frame_path=frames[0].path,
                    timestamp=frames[0].timestamp_sec,
                    keep=True,
                    reason="ok",
                )
            ]

    monkeypatch.setattr(module, "VisionStage1", FakeStage1)
    monkeypatch.setattr(module, "VisionStage2", FakeStage2)
    monkeypatch.setattr(module, "extract_new_frames", lambda *a, **k: [])

    chunk = SRTChunk(
        index=0,
        start_sec=0.0,
        end_sec=10.0,
        segments=[TranscriptSegment(text="hello", start=0.0, end=10.0)],
    )

    blog, decisions, ok = asyncio.run(
        ChunkedDocGenerator()._process_chunk(
            chunk, heuristic, video_path="unused.mp4", temp_frames=tmp_path
        )
    )

    assert ok is True
    assert blog == "blog"
    assert captured["frames"][0].timestamp_sec == 5.7
    assert captured["srt_ids"] == [1]
    assert decisions[0].keep is True


def test_process_chunk_respects_stage1_deletion(tmp_path, monkeypatch):
    """Heuristic frames deleted by Stage1 must not reach Stage2."""
    import framelearn.pipeline.chunked_doc_generator as module

    frame = tmp_path / "frame_00h00m05s000ms_scene_001.jpg"
    _write_jpg(frame)
    heuristic = [
        CandidateFrame(path=str(frame), timestamp_sec=5.0, source="heuristic")
    ]

    s1_out = VisionStage1Output(blog_markdown="blog", selected_timestamps=[])

    class FakeStage1:
        def __init__(self, max_images=None):
            pass

        async def process(self, chunk, frames):
            return s1_out

    captured = {}

    class FakeStage2:
        async def process(self, chunk, frames, srt_id_per_frame):
            captured["frames"] = list(frames)
            return []

    monkeypatch.setattr(module, "VisionStage1", FakeStage1)
    monkeypatch.setattr(module, "VisionStage2", FakeStage2)
    monkeypatch.setattr(module, "extract_new_frames", lambda *a, **k: [])

    chunk = SRTChunk(
        index=0,
        start_sec=0.0,
        end_sec=10.0,
        segments=[TranscriptSegment(text="hello", start=0.0, end=10.0)],
    )

    blog, decisions, ok = asyncio.run(
        ChunkedDocGenerator()._process_chunk(
            chunk, heuristic, video_path="unused.mp4", temp_frames=tmp_path
        )
    )
    assert ok is True
    assert captured["frames"] == []
    assert decisions == []
