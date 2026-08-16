"""Integration test for the anchored blog orchestration (offline)."""

import asyncio
from pathlib import Path

from framelearn.pipeline.asr_adapter import TranscriptSegment
from framelearn.pipeline.blog_generator import BlogGeneratorOutput, FrameRequest
from framelearn.pipeline.chunked_doc_generator import ChunkedDocGenerator
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.vision_frame_evaluator import FrameEvaluation


def _write_jpg(path: Path):
    path.write_bytes(b"\xff\xd8\xff\xd9")


def test_anchored_pipeline_generates_both_markdown_files(tmp_path, monkeypatch):
    import framelearn.pipeline.chunked_doc_generator as module

    candidate = tmp_path / "candidate.jpg"
    _write_jpg(candidate)

    segments = [
        TranscriptSegment(text="第一条字幕", start=0.0, end=5.0),
        TranscriptSegment(text="第二条字幕", start=5.0, end=10.0),
    ]
    pre_extracted = [
        CandidateFrame(path=str(candidate), timestamp_sec=3.0, source="heuristic")
    ]

    class FakeBlogGenerator:
        def __init__(self, *args, **kwargs):
            pass

        async def generate(self, chunk, frames):
            assert frames and frames[0].path == str(candidate)
            return BlogGeneratorOutput(
                blog_markdown="博客正文 [[FRAME:a1@3.0]]",
                frame_requests=[
                    FrameRequest(
                        anchor_id="a1",
                        srt_id=1,
                        timestamp=3.0,
                        request_type="reuse",
                        source_frame_path=str(candidate),
                        reason="test",
                    )
                ],
            )

    class FakeVisionFrameEvaluator:
        def __init__(self, *args, **kwargs):
            pass

        async def evaluate(self, items, video_path, temp_frames):
            return [
                FrameEvaluation(
                    anchor_id=item.anchor_id,
                    srt_id=item.srt_id,
                    frame_path=item.frame_path,
                    timestamp=item.timestamp,
                    keep_image=True,
                    content_type="diagram",
                    caption="测试图片",
                    text_representation="",
                    reason="ok",
                )
                for item in items
            ]

    monkeypatch.setattr(module, "BlogGenerator", FakeBlogGenerator)
    monkeypatch.setattr(module, "VisionFrameEvaluator", FakeVisionFrameEvaluator)

    output_dir = tmp_path / "out"
    result = asyncio.run(
        ChunkedDocGenerator().generate(
            video_path=str(tmp_path / "unused.mp4"),
            srt_segments=segments,
            output_dir=output_dir,
            video_title="测试视频",
            pre_extracted_frames=pre_extracted,
        )
    )

    assert result.srt_picture_path.exists()
    assert result.blog_path.exists()
    assert result.chunks_succeeded == 1

    blog_text = result.blog_path.read_text(encoding="utf-8")
    assert "candidate.jpg" in blog_text
    assert "测试图片" in blog_text
    assert "FRAME" not in blog_text

    srt_text = result.srt_picture_path.read_text(encoding="utf-8")
    assert "第一条字幕" in srt_text
    assert "candidate.jpg" in srt_text


def test_one_chunk_failure_does_not_block_other_chunks(tmp_path, monkeypatch):
    """One chunk's text-model failure should degrade only that chunk."""
    import framelearn.pipeline.chunked_doc_generator as module

    segments = [
        TranscriptSegment(text="第一段正常字幕", start=0.0, end=5.0),
        TranscriptSegment(text="第二段原始字幕", start=600.0, end=605.0),
    ]
    vision_calls = []

    class FlakyBlogGenerator:
        def __init__(self, *args, **kwargs):
            pass

        async def generate(self, chunk, frames):
            if chunk.index == 0:
                return BlogGeneratorOutput(
                    blog_markdown="第一段正文",
                    frame_requests=[],
                )
            raise RuntimeError("text model unavailable")

    class FakeVisionFrameEvaluator:
        def __init__(self, *args, **kwargs):
            pass

        async def evaluate(self, items, video_path, temp_frames):
            vision_calls.append(list(items))
            return []

    monkeypatch.setattr(module, "BlogGenerator", FlakyBlogGenerator)
    monkeypatch.setattr(module, "VisionFrameEvaluator", FakeVisionFrameEvaluator)

    result = asyncio.run(
        ChunkedDocGenerator(segment_minutes=10, concurrency=2).generate(
            video_path=str(tmp_path / "unused.mp4"),
            srt_segments=segments,
            output_dir=tmp_path / "out",
            video_title="多段测试",
            pre_extracted_frames=[],
        )
    )

    assert result.chunks_total == 2
    assert result.chunks_succeeded == 1
    assert result.failed_chunks == [1]

    blog_text = result.blog_path.read_text(encoding="utf-8")
    assert "第一段正文" in blog_text
    assert "第二段原始字幕" in blog_text
    assert vision_calls == []
