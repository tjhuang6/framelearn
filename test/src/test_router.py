"""Unit tests for CommandRouter."""

from unittest.mock import MagicMock, patch

import pytest

from framelearn.router import CommandRouter

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def make_router(workspace="."):
    router = CommandRouter(workspace=workspace)
    return router


# ------------------------------------------------------------------
# Command dispatch
# ------------------------------------------------------------------

class TestCommandDispatch:
    def test_unknown_command_raises(self):
        router = make_router()
        with pytest.raises(ValueError, match="未知命令"):
            router.execute("unknown_cmd arg")

    def test_help_prints(self, capsys):
        router = make_router()
        router.execute("help")
        out = capsys.readouterr().out
        assert "framelearn" in out.lower() or "FrameLearn" in out

    def test_summarize_prints(self, capsys):
        router = make_router()
        router.execute("summarize")
        out = capsys.readouterr().out
        assert "summarize" in out.lower() or "总结" in out


# ------------------------------------------------------------------
# run command
# ------------------------------------------------------------------

class TestRunCommand:
    def test_missing_source_raises(self):
        router = make_router()
        with pytest.raises(ValueError, match="缺少视频"):
            router.execute("run")

    def test_invalid_url_raises(self):
        router = make_router()
        with pytest.raises(ValueError, match="无效的视频链接"):
            router.execute("run https://notsupported.com/video")

    def test_youtube_url_downloads_then_calls_pipeline(self, tmp_path, capsys):
        video = tmp_path / "youtube-video.mp4"
        video.write_bytes(b"\x00")
        from framelearn.downloaders import DownloadedVideo
        from framelearn.pipeline import PipelineResult

        downloaded = DownloadedVideo(
            video_path=video,
            source_url="https://youtube.com/watch?v=abc12345678",
            platform="youtube",
            video_id="abc12345678",
            title="Tutorial",
        )
        mock_result = PipelineResult(
            output_dir=tmp_path,
            srt_picture_path=tmp_path / "srt_picture.md",
            blog_path=tmp_path / "blog.md",
            keyframes=[],
            subtitle_text="",
            error=None,
        )

        with (
            patch("framelearn.router.download_video", return_value=downloaded) as mock_download,
            patch("framelearn.pipeline.VideoPipeline") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_instance.run.return_value = mock_result
            mock_cls.return_value = mock_instance

            router = make_router()
            router.execute("run https://youtube.com/watch?v=abc12345678")

        mock_download.assert_called_once_with("https://youtube.com/watch?v=abc12345678")
        out = capsys.readouterr().out
        assert "Tutorial" in out
        assert "输出目录" in out

    def test_bilibili_url_downloads_then_calls_pipeline(self, tmp_path):
        video = tmp_path / "BV1xx.mp4"
        video.write_bytes(b"\x00")
        from framelearn.downloaders import DownloadedVideo
        from framelearn.pipeline import PipelineResult

        downloaded = DownloadedVideo(
            video_path=video,
            source_url="https://bilibili.com/video/BV1xx",
            platform="bilibili",
            video_id="BV1xx",
        )
        mock_result = PipelineResult(
            output_dir=tmp_path,
            srt_picture_path=tmp_path / "srt_picture.md",
            blog_path=tmp_path / "blog.md",
            keyframes=[],
            subtitle_text="",
            error=None,
        )

        with (
            patch("framelearn.router.download_video", return_value=downloaded) as mock_download,
            patch("framelearn.pipeline.VideoPipeline") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_instance.run.return_value = mock_result
            mock_cls.return_value = mock_instance

            router = make_router()
            router.execute("run https://bilibili.com/video/BV1xx")

        mock_download.assert_called_once_with("https://bilibili.com/video/BV1xx")

    def test_douyin_share_text_is_normalized_before_download(self, tmp_path):
        video = tmp_path / "douyin.mp4"
        video.write_bytes(b"\x00")
        from framelearn.downloaders import DownloadedVideo
        from framelearn.pipeline import PipelineResult

        downloaded = DownloadedVideo(
            video_path=video,
            source_url="https://v.douyin.com/abc/",
            platform="douyin",
            video_id="123",
        )
        mock_result = PipelineResult(
            output_dir=tmp_path,
            srt_picture_path=tmp_path / "srt_picture.md",
            blog_path=tmp_path / "blog.md",
            keyframes=[],
            subtitle_text="",
            error=None,
        )

        with (
            patch("framelearn.router.download_video", return_value=downloaded) as mock_download,
            patch("framelearn.pipeline.VideoPipeline") as mock_cls,
        ):
            mock_instance = MagicMock()
            mock_instance.run.return_value = mock_result
            mock_cls.return_value = mock_instance
            router = make_router()
            router.execute(
                "run 7.43 复制此链接 https://v.douyin.com/abc/ 打开Dou音搜索"
            )

        mock_download.assert_called_once_with("https://v.douyin.com/abc/")

    def test_nonexistent_file_raises(self):
        router = make_router()
        with pytest.raises(ValueError, match="文件不存在"):
            router.execute("run /nonexistent/video.mp4")

    def test_unsupported_format_raises(self, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_text("dummy")
        router = make_router()
        with pytest.raises(ValueError, match="不支持的文件格式"):
            router.execute(f"run {pdf}")

    def test_valid_local_mp4_calls_pipeline(self, tmp_path, capsys):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        router = make_router()

        # Mock the VideoPipeline class itself
        from framelearn.pipeline import PipelineResult
        mock_result = PipelineResult(
            output_dir=tmp_path,
            srt_picture_path=tmp_path / "srt_picture.md",
            blog_path=tmp_path / "blog.md",
            keyframes=[],
            subtitle_text="",
            error=None,
        )

        with patch('framelearn.pipeline.VideoPipeline') as mock_cls:
            mock_instance = MagicMock()
            mock_instance.run.return_value = mock_result
            mock_cls.return_value = mock_instance

            router.execute(f"run {video}")
            out = capsys.readouterr().out
            assert "输出目录" in out or "教材" in out

    def test_local_mp4_pipeline_error_raises(self, tmp_path):
        """A business failure inside VideoPipeline.run() must surface as a
        domain exception, not a silent return, so the CLI can map it to a
        nonzero exit code."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00")
        router = make_router()

        from framelearn.errors import PipelineExecutionError
        from framelearn.pipeline import PipelineResult
        mock_result = PipelineResult(
            output_dir=tmp_path,
            srt_picture_path=tmp_path / "srt_picture.md",
            blog_path=tmp_path / "blog.md",
            keyframes=[],
            subtitle_text="",
            error="FFmpeg 未安装，请先安装：brew install ffmpeg",
        )

        with patch('framelearn.pipeline.VideoPipeline') as mock_cls:
            mock_instance = MagicMock()
            mock_instance.run.return_value = mock_result
            mock_cls.return_value = mock_instance

            with pytest.raises(PipelineExecutionError, match="FFmpeg"):
                router.execute(f"run {video}")


# ------------------------------------------------------------------
# ask command
# ------------------------------------------------------------------

class TestAskCommand:
    def test_empty_question_raises(self):
        router = make_router()
        with pytest.raises(ValueError, match="缺少问题内容"):
            router.execute("ask")

    @patch("framelearn.provider_adapter.call_text_llm")
    def test_question_calls_api(self, mock_call_text_llm):
        mock_call_text_llm.return_value = "Answer from API"
        router = make_router()
        router.execute("ask 什么是装饰器")
        mock_call_text_llm.assert_called_once()
        call_args = mock_call_text_llm.call_args
        assert "装饰器" in call_args[0][0]


# ------------------------------------------------------------------
# URL / file validation helpers
# ------------------------------------------------------------------

class TestValidation:
    def setup_method(self):
        self.router = make_router()

    def test_youtube_url_valid(self):
        assert self.router._is_valid_video_url("https://youtube.com/watch?v=xxx") is True

    def test_youtu_be_valid(self):
        assert self.router._is_valid_video_url("https://youtu.be/xxx") is True

    def test_bilibili_valid(self):
        assert self.router._is_valid_video_url("https://bilibili.com/video/BV1") is True

    def test_bilibili_short_link_valid(self):
        assert self.router._is_valid_video_url("https://b23.tv/abc") is True

    def test_douyin_valid(self):
        assert self.router._is_valid_video_url("https://v.douyin.com/abc/") is True

    def test_douyin_video_page_valid(self):
        assert self.router._is_valid_video_url("https://www.douyin.com/video/123") is True

    def test_kuaishou_valid(self):
        assert self.router._is_valid_video_url("https://v.kuaishou.com/abc") is True

    def test_random_url_invalid(self):
        assert self.router._is_valid_video_url("https://example.com/video") is False

    def test_mp4_is_video(self):
        assert self.router._is_video_file("/path/video.mp4") is True

    def test_mkv_is_video(self):
        assert self.router._is_video_file("/path/video.mkv") is True

    def test_pdf_is_not_video(self):
        assert self.router._is_video_file("/path/doc.pdf") is False

    def test_case_insensitive(self):
        assert self.router._is_video_file("/path/VIDEO.MP4") is True
