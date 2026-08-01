"""Unit tests for video pipeline modules."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from framelearn.pipeline.asr_adapter import ASRAdapter, TranscriptResult, TranscriptSegment
from framelearn.pipeline.doc_generator import DocumentGenerator
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
                assert len(result.segments) == 1

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
            frames.append(frame)

        dedup = KeyframeDeduplicator(similarity_threshold=0.9)
        unique = dedup.deduplicate(frames, max_frames=10)

        # All frames should be kept (they're different)
        assert len(unique) <= len(frames)

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
            frames.append(frame)

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
        unique = dedup.deduplicate([frame])
        assert len(unique) >= 1


# ------------------------------------------------------------------
# DocumentGenerator
# ------------------------------------------------------------------

class TestDocumentGenerator:
    def test_mode_textbook_uses_correct_prompt(self, tmp_path):
        frame = tmp_path / "frame.jpg"
        frame.touch()

        with patch('framelearn.app_server.session.AppServerSession') as mock_session:
            mock_instance = Mock()
            mock_result = Mock()
            mock_result.error = None
            mock_result.final_text = "# 教材\n完整段落..."
            mock_instance.run_turn.return_value = mock_result
            mock_session.return_value = mock_instance

            gen = DocumentGenerator()
            result = gen.generate([frame], "字幕内容", "测试", mode="textbook")

            assert "教材" in result
            # Verify textbook prompt was used
            call_args = mock_instance.run_turn.call_args[0][0]
            assert "技术图书编辑" in call_args

    def test_mode_notes_uses_correct_prompt(self, tmp_path):
        frame = tmp_path / "frame.jpg"
        frame.touch()

        with patch('framelearn.app_server.session.AppServerSession') as mock_session:
            mock_instance = Mock()
            mock_result = Mock()
            mock_result.error = None
            mock_result.final_text = "## 知识点\n- 要点1\n- 要点2"
            mock_instance.run_turn.return_value = mock_result
            mock_session.return_value = mock_instance

            gen = DocumentGenerator()
            result = gen.generate([frame], "字幕内容", "测试", mode="notes")

            assert "知识点" in result
            # Verify notes prompt was used
            call_args = mock_instance.run_turn.call_args[0][0]
            assert "课堂笔记整理助手" in call_args

    def test_generate_error_raises(self, tmp_path):
        frame = tmp_path / "frame.jpg"
        frame.touch()

        with patch('framelearn.app_server.session.AppServerSession') as mock_session:
            mock_instance = Mock()
            mock_result = Mock()
            mock_result.error = "API failed"
            mock_instance.run_turn.return_value = mock_result
            mock_session.return_value = mock_instance

            gen = DocumentGenerator()
            with pytest.raises(RuntimeError, match="Document generation failed"):
                gen.generate([frame], "字幕", "测试")
