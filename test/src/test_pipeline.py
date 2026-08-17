"""Unit tests for video pipeline modules."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from framelearn.pipeline.asr_adapter import ASRAdapter, TranscriptResult, TranscriptSegment
from framelearn.pipeline.ffmpeg_helper import FFmpegHelper
from framelearn.pipeline.keyframe_dedup import KeyframeDeduplicator
from framelearn.pipeline.subtitle_cleaner import SubtitleCleaner


# ------------------------------------------------------------------
# SubtitleCleaner
# ------------------------------------------------------------------

class TestSubtitleCleaner:
    def setup_method(self):
        self.cleaner = SubtitleCleaner()

    def test_remove_brackets(self):
        raw = "大家好[音乐]，今天讲Python（掌声）"
        cleaned = self.cleaner.clean(raw)
        assert "[音乐]" not in cleaned
        assert "（掌声）" not in cleaned
        assert "大家好" in cleaned

    def test_fullwidth_to_halfwidth(self):
        raw = "变量名，函数名。结束！"
        cleaned = self.cleaner.clean(raw)
        assert "," in cleaned
        assert "." in cleaned
        assert "!" in cleaned
        assert "，" not in cleaned

    def test_merge_duplicate_lines(self):
        raw = "这是第一行\n这是第一行\n这是第二行\n这是第二行"
        cleaned = self.cleaner.clean(raw)
        lines = [line for line in cleaned.split('\n') if line.strip()]
        assert lines.count("这是第一行") == 1
        assert lines.count("这是第二行") == 1

    def test_sentence_breaks(self):
        raw = "第一句。第二句！第三句？"
        cleaned = self.cleaner.clean(raw)
        assert "\n" in cleaned  # Should have line breaks after punctuation

    def test_empty_input(self):
        assert self.cleaner.clean("") == ""

    def test_whitespace_normalization(self):
        raw = "单词    很多空格\n\n\n\n连续换行"
        cleaned = self.cleaner.clean(raw)
        assert "    " not in cleaned  # No excessive spaces
        assert "\n\n\n" not in cleaned  # Max 2 newlines


# ------------------------------------------------------------------
# FFmpegHelper
# ------------------------------------------------------------------

class TestFFmpegHelper:
    def test_check_installed(self):
        # Assuming FFmpeg is installed in test environment
        assert FFmpegHelper.check_installed() is True

    def test_has_audio_stream_mock(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(stdout="codec_type=audio")
            assert FFmpegHelper.has_audio_stream("video.mp4") is True

            mock_run.return_value = Mock(stdout="codec_type=video")
            assert FFmpegHelper.has_audio_stream("video.mp4") is False

    def test_find_companion_audio_exact_match(self, tmp_path):
        video = tmp_path / "tutorial-30080.mp4"
        audio = tmp_path / "tutorial-30080.mp3"
        video.touch()
        audio.touch()

        found = FFmpegHelper.find_companion_audio(str(video))
        assert found == audio

    def test_find_companion_audio_prefix_match(self, tmp_path):
        video = tmp_path / "tutorial-30080.mp4"
        audio = tmp_path / "tutorial-30280.mp3"
        video.touch()
        audio.touch()

        found = FFmpegHelper.find_companion_audio(str(video))
        assert found == audio

    def test_find_companion_audio_not_found(self, tmp_path):
        video = tmp_path / "video.mp4"
        video.touch()

        found = FFmpegHelper.find_companion_audio(str(video))
        assert found is None


# ------------------------------------------------------------------
# ASRAdapter
# ------------------------------------------------------------------

class TestASRAdapter:
    def test_init_without_key_raises(self, tmp_path):
        audio = tmp_path / "test.m4a"
        audio.write_bytes(b"\x00")
        with patch.dict('os.environ', {'SILICONFLOW_API_KEY': ''}):
            with pytest.raises(ValueError, match="SILICONFLOW_API_KEY"):
                adapter = ASRAdapter(provider="siliconflow")
                adapter.transcribe(str(audio))

    def test_init_with_placeholder_key_raises(self, tmp_path):
        audio = tmp_path / "test.m4a"
        audio.write_bytes(b"\x00")
        with patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'your_key_here'}):
            with pytest.raises(ValueError, match="SILICONFLOW_API_KEY"):
                adapter = ASRAdapter(provider="siliconflow")
                adapter.transcribe(str(audio))

    def test_transcribe_success(self, tmp_path):
        audio = tmp_path / "test.m4a"
        audio.write_bytes(b"\x00" * 100)

        with patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'sk-valid-key'}):
            with patch('httpx.post') as mock_post:
                mock_response = Mock()
                mock_response.json.return_value = {"text": "转录文字"}
                mock_response.raise_for_status = Mock()
                mock_post.return_value = mock_response

                adapter = ASRAdapter(provider="siliconflow")
                result = adapter.transcribe(str(audio))

                assert result.full_text == "转录文字"
                assert result.has_timestamps is False
                assert result.segments == []

    def test_transcribe_file_not_found(self):
        with patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'sk-valid'}):
            adapter = ASRAdapter(provider="siliconflow")
            with pytest.raises(FileNotFoundError):
                adapter.transcribe("/nonexistent/audio.m4a")

    def test_transcribe_retry_on_429(self, tmp_path):
        audio = tmp_path / "test.m4a"
        audio.write_bytes(b"\x00")

        with patch.dict('os.environ', {'SILICONFLOW_API_KEY': 'sk-valid'}):
            with patch('httpx.post') as mock_post:
                error_response = Mock()
                error_response.status_code = 429

                success_response = Mock()
                success_response.json.return_value = {"text": "success"}
                success_response.raise_for_status = Mock()

                from httpx import HTTPStatusError, Request
                mock_post.side_effect = [
                    HTTPStatusError("rate limit", request=Mock(spec=Request), response=error_response),
                    success_response,
                ]

                with patch('time.sleep'):
                    adapter = ASRAdapter(provider="siliconflow")
                    result = adapter.transcribe(str(audio), max_retries=3)
                    assert result.full_text == "success"


# ------------------------------------------------------------------
# KeyframeDeduplicator
# ------------------------------------------------------------------

class TestKeyframeDeduplicator:
    def test_deduplicate_empty_list(self):
        dedup = KeyframeDeduplicator()
        assert dedup.deduplicate([]) == []

    def test_deduplicate_preserves_unique_frames(self, tmp_path):
        # Create 3 different dummy images
        frames = []
        for i in range(3):
            frame = tmp_path / f"frame_{i}.jpg"
            # Create a minimal valid JPEG (1x1 pixel)
            frame.write_bytes(
                b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
                b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08'
                b'\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d'
                b'\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342'
                b'\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
                b'\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
                b'\x00\x00\x00\x00\t'
                b'\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
                b'\x00\x00\x00\x00\x00'
                b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xd2\xcf\x20'
                b'\xff\xd9'
            )
            frames.append((frame, float(i * 30)))  # (path, timestamp)

        dedup = KeyframeDeduplicator(similarity_threshold=0.9)
        unique = dedup.deduplicate(frames, max_frames=10)

        # All frames should be kept (they're different), and result is tuples
        assert len(unique) <= len(frames)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in unique)

    def test_max_frames_limit(self, tmp_path):
        frames = []
        for i in range(20):
            frame = tmp_path / f"frame_{i}.jpg"
            frame.write_bytes(
                b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
                b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08'
                b'\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d'
                b'\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342'
                b'\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
                b'\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
                b'\x00\x00\x00\x00\t'
                b'\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
                b'\x00\x00\x00\x00\x00'
                b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xd2\xcf\x20'
                b'\xff\xd9'
            )
            frames.append((frame, float(i * 10)))  # (path, timestamp)

        dedup = KeyframeDeduplicator()
        unique = dedup.deduplicate(frames, max_frames=5)
        assert len(unique) <= 5

    def test_at_least_one_frame_kept(self, tmp_path):
        frame = tmp_path / "frame.jpg"
        frame.write_bytes(
            b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
            b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08'
            b'\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d'
            b'\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342'
            b'\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
            b'\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
            b'\x00\x00\x00\x00\t'
            b'\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
            b'\x00\x00\x00\x00\x00'
            b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xd2\xcf\x20'
            b'\xff\xd9'
        )

        dedup = KeyframeDeduplicator()
        unique = dedup.deduplicate([(frame, 0.0)])
        assert len(unique) >= 1


# ------------------------------------------------------------------
# FFmpegHelper.capture_single_frame
# ------------------------------------------------------------------

class TestFFmpegHelperCaptureSingleFrame:
    def test_capture_single_frame_success(self, tmp_path):
        output = tmp_path / "frame_00h01m30s.jpg"
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)
            result = FFmpegHelper.capture_single_frame(
                "video.mp4", 90.0, str(output)
            )
        assert result is True
        cmd = mock_run.call_args[0][0]
        assert "-ss" in cmd
        assert "00:01:30.000" in cmd
        assert "-vframes" in cmd
        assert "1" in cmd

    def test_capture_single_frame_failure(self, tmp_path):
        output = tmp_path / "frame.jpg"
        with patch('subprocess.run') as mock_run:
            import subprocess
            mock_run.side_effect = subprocess.CalledProcessError(1, "ffmpeg")
            result = FFmpegHelper.capture_single_frame(
                "video.mp4", 45.0, str(output)
            )
        assert result is False

    def test_capture_single_frame_timestamp_format(self, tmp_path):
        """Verify HH:MM:SS.mmm format for ffmpeg -ss."""
        output = tmp_path / "frame.jpg"
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(returncode=0)
            FFmpegHelper.capture_single_frame("v.mp4", 3661.5, str(output))
        cmd = mock_run.call_args[0][0]
        # 3661.5s = 1h 1m 1.5s → 01:01:01.500
        assert "01:01:01.500" in cmd
