"""End-to-end integration test for VideoPipeline.

Exercises the full pipeline with FFmpeg, ASR, keyframe extraction, and
document generation all mocked, so the test stays offline. The goal is
to verify that the pipeline orchestrates its modules correctly:

  1. FFmpeg/FFprobe is checked first; failure short-circuits with a clear error.
  2. Audio extraction + ASR are skipped when --subtitle is given.
  3. Subtitle text + SRT are written when timestamps are available.
  4. The chunked doc generator produces srt_picture.md and blog.md.
  5. Keyframes listed in PipelineResult all exist on disk.
  6. The run-report.json is written to the output dir.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from framelearn.pipeline.asr_adapter import TranscriptResult, TranscriptSegment
from framelearn.pipeline.video_pipeline import VideoPipeline


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _write_fake_jpeg(path):
    """Write a minimal valid JPEG so file-size checks don't trip."""
    path.write_bytes(
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08"
        b"\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d"
        b"\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\t"
        b"\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xd2\xcf\x20"
        b"\xff\xd9"
    )


class _FakeChunkedDocGen:
    """Stub for ChunkedDocGenerator.

    Writes the current output contract (srt_picture.md / blog.md) plus a
    few fake keyframes so the real VideoPipeline post-processing and
    manifest code paths are still exercised.
    """

    def __init__(self, *args, **kwargs):
        self.received_segments = None
        self.received_pre_extracted = None

    async def generate(
        self,
        video_path,
        srt_segments,
        output_dir,
        video_title="视频讲义",
        pre_extracted_frames=None,
    ):
        output_dir = __import__("pathlib").Path(output_dir)
        src_dir = output_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        self.received_segments = list(srt_segments)
        self.received_pre_extracted = (
            list(pre_extracted_frames) if pre_extracted_frames else None
        )

        frame_paths = []
        for i, timestamp in enumerate([10.0, 30.0, 60.0]):
            frame = src_dir / f"frame_00h00m{int(timestamp):02d}s000ms_interval_{i+1:03d}.jpg"
            _write_fake_jpeg(frame)
            frame_paths.append(frame)

        srt_path = output_dir / "srt_picture.md"
        blog_path = output_dir / "blog.md"
        srt_path.write_text(f"# {video_title}\n\nsrt picture\n", encoding="utf-8")
        blog_path.write_text(f"# {video_title}\n\nblog\n", encoding="utf-8")

        return SimpleNamespace(
            output_dir=output_dir,
            srt_picture_path=srt_path,
            blog_path=blog_path,
            chunks_total=1,
            chunks_succeeded=1,
            failed_chunks=[],
        )


def _stub_modules(monkeypatch, tmp_path):
    """Patch the heavy I/O modules so the pipeline can run end-to-end
    against a fake video file."""
    # 1. FFmpeg/ffprobe are "installed"
    monkeypatch.setattr(
        "framelearn.pipeline.video_pipeline.FFmpegHelper.check_installed",
        staticmethod(lambda: True),
    )

    # 2. Audio extraction succeeds and writes a fake file
    def fake_extract_audio(video, out):
        from pathlib import Path
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"\x00" * 100)
        return True

    monkeypatch.setattr(
        "framelearn.pipeline.video_pipeline.FFmpegHelper.extract_audio",
        staticmethod(fake_extract_audio),
    )

    # 3. ASR returns a transcript with timestamps (mimics dashscope)
    fake_transcript = TranscriptResult(
        segments=[
            TranscriptSegment(text="大家好", start=0.0, end=1.5),
            TranscriptSegment(text="今天讲 Python", start=1.5, end=4.0),
        ],
        full_text="大家好 今天讲 Python",
        has_timestamps=True,
        srt="1\n00:00:00,000 --> 00:00:01,500\n大家好\n\n"
            "2\n00:00:01,500 --> 00:00:04,000\n今天讲 Python",
    )

    asr_calls = []

    class FakeASR:
        provider = "dashscope"
        model = "qwen-audio"

        def transcribe(self, audio_path, output_dir=None):
            asr_calls.append(audio_path)
            return fake_transcript

    monkeypatch.setattr(
        "framelearn.pipeline.video_pipeline.ASRAdapter",
        lambda *a, **kw: FakeASR(),
    )

    # 4. The chunked doc generator is the current output writer.
    monkeypatch.setattr(
        "framelearn.pipeline.chunked_doc_generator.ChunkedDocGenerator",
        _FakeChunkedDocGen,
    )

    # 5. Skip provider config loading side-effects.
    monkeypatch.setattr(
        "framelearn.provider_adapter.load_text_config",
        lambda: MagicMock(provider="fake", model="fake-model"),
    )
    monkeypatch.setattr(
        "framelearn.provider_adapter.load_vision_config",
        lambda: MagicMock(provider="fake", model="fake-model"),
    )

    return fake_transcript, asr_calls


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestEndToEnd:
    def test_ffmpeg_missing_short_circuits_with_clear_error(self, tmp_path, monkeypatch):
        """First thing the pipeline checks is FFmpeg/ffprobe. If missing,
        it must return early with a user-actionable error."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "out"

        pipeline = VideoPipeline(str(video), output_dir=str(output_dir))
        monkeypatch.setattr(
            "framelearn.pipeline.video_pipeline.FFmpegHelper.check_installed",
            staticmethod(lambda: False),
        )

        result = pipeline.run()

        assert result.error is not None
        assert "FFmpeg/FFprobe" in result.error
        assert not (output_dir / "srt_picture.md").exists()
        assert not (output_dir / "blog.md").exists()

    def test_full_run_with_subtitle_skips_asr(self, tmp_path, monkeypatch):
        """When --subtitle is supplied, ASR is bypassed entirely."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        subtitle = tmp_path / "subtitle.srt"
        subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:02,000\n大家好\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\n今天讲 Python\n",
            encoding="utf-8",
        )
        output_dir = tmp_path / "out"

        _, asr_calls = _stub_modules(monkeypatch, tmp_path)

        pipeline = VideoPipeline(
            str(video), output_dir=str(output_dir), subtitle_path=str(subtitle)
        )
        result = pipeline.run()

        assert result.error is None, f"unexpected error: {result.error}"
        assert asr_calls == []
        assert (output_dir / "srt_picture.md").exists()
        assert (output_dir / "blog.md").exists()
        assert (output_dir / "src" / "subtitle.txt").exists()
        assert (output_dir / "src" / "subtitle.srt").exists()

    def test_full_run_writes_required_outputs(self, tmp_path, monkeypatch):
        """End-to-end: fake video → fake ASR → fake chunked doc generator.
        Verify the output directory layout matches the current design."""
        video = tmp_path / "lecture.mp4"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "out"

        _stub_modules(monkeypatch, tmp_path)
        pipeline = VideoPipeline(str(video), output_dir=str(output_dir))

        result = pipeline.run()

        assert result.error is None, f"unexpected error: {result.error}"
        assert result.markdown_path.exists()
        assert (output_dir / "srt_picture.md").exists()
        assert (output_dir / "blog.md").exists()
        assert (output_dir / "src" / "subtitle.txt").exists()
        assert (output_dir / "run-report.json").exists()

    def test_keyframes_copied_to_src_with_timestamp_filenames(
        self, tmp_path, monkeypatch
    ):
        """Every keyframe listed in PipelineResult must exist on disk."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "out"

        _stub_modules(monkeypatch, tmp_path)
        pipeline = VideoPipeline(str(video), output_dir=str(output_dir))
        result = pipeline.run()

        assert result.error is None, f"unexpected error: {result.error}"
        src_frames = list((output_dir / "src").glob("*.jpg"))
        assert len(src_frames) >= 1
        assert result.keyframes
        for kf in result.keyframes:
            assert kf.exists()

    def test_pipeline_result_warnings_default_to_empty(self, tmp_path, monkeypatch):
        """A clean run with no degradation events must produce an empty
        warnings list on PipelineResult."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "out"

        _stub_modules(monkeypatch, tmp_path)
        pipeline = VideoPipeline(str(video), output_dir=str(output_dir))
        result = pipeline.run()

        assert result.error is None, f"unexpected error: {result.error}"
        assert result.warnings == []

    def test_audio_extraction_failure_returns_error(self, tmp_path, monkeypatch):
        """If FFmpeg cannot extract audio, the pipeline must surface a clear
        error rather than crash."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "out"

        monkeypatch.setattr(
            "framelearn.pipeline.video_pipeline.FFmpegHelper.check_installed",
            staticmethod(lambda: True),
        )
        monkeypatch.setattr(
            "framelearn.pipeline.video_pipeline.FFmpegHelper.extract_audio",
            staticmethod(lambda v, o: False),
        )

        pipeline = VideoPipeline(str(video), output_dir=str(output_dir))
        result = pipeline.run()

        assert result.error is not None
        assert "音轨提取失败" in result.error

    def test_full_run_without_srt_timestamps_synthesizes_segments(
        self, tmp_path, monkeypatch
    ):
        """SiliconFlow-style ASR has no timestamps.

        The pipeline must synthesize timestamped segments (not crash on an
        unbound ``srt_path``) and still produce both Markdown outputs.
        """
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        output_dir = tmp_path / "out"

        _stub_modules(monkeypatch, tmp_path)

        untimed = TranscriptResult(
            segments=[],
            full_text="大家好\n今天讲 Python",
            has_timestamps=False,
            srt=None,
        )

        class FakeASR:
            provider = "siliconflow"
            model = "sensevoice"

            def transcribe(self, audio_path, output_dir=None):
                return untimed

        monkeypatch.setattr(
            "framelearn.pipeline.video_pipeline.ASRAdapter",
            lambda *a, **kw: FakeASR(),
        )
        monkeypatch.setattr(
            "framelearn.pipeline.video_pipeline.FFmpegHelper.get_duration",
            staticmethod(lambda path, fallback=0.0: 10.0),
        )

        pipeline = VideoPipeline(str(video), output_dir=str(output_dir))
        result = pipeline.run()

        assert result.error is None, f"unexpected error: {result.error}"
        assert (output_dir / "srt_picture.md").exists()
        assert (output_dir / "blog.md").exists()
        assert not (output_dir / "src" / "subtitle.srt").exists()
        report = json.loads((output_dir / "run-report.json").read_text(encoding="utf-8"))
        assert report["status"] == "success"
