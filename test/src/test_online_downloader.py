"""Unit tests for online video downloader helpers."""

from pathlib import Path

import pytest

from framelearn.downloaders import (
    detect_platform,
    extract_url,
    extract_video_id,
    is_supported_video_url,
    online,
)
from framelearn.errors import DownloadError


class TestUrlParser:
    def test_extract_url_from_plain_text(self):
        text = "7.43 复制此链接 https://v.douyin.com/0pcFVdG_lx4/ 打开Dou音搜索"
        assert extract_url(text) == "https://v.douyin.com/0pcFVdG_lx4/"

    def test_extract_url_stops_before_chinese_text(self):
        text = "https://v.kuaishou.com/abc复制此链接"
        assert extract_url(text) == "https://v.kuaishou.com/abc"

    def test_detect_platform(self):
        assert detect_platform("https://youtube.com/watch?v=abc") == "youtube"
        assert detect_platform("https://b23.tv/abc") == "bilibili"
        assert detect_platform("https://v.douyin.com/abc/") == "douyin"
        assert detect_platform("https://www.kuaishou.com/short-video/abc") == "kuaishou"

    def test_is_supported(self):
        assert is_supported_video_url("https://youtu.be/abc") is True
        assert is_supported_video_url("https://example.com/video") is False

    def test_extract_video_ids(self):
        assert extract_video_id(
            "https://www.bilibili.com/video/BV1GJ411x7h7", "bilibili"
        ) == "BV1GJ411x7h7"
        assert (
            extract_video_id("https://youtu.be/abcdefghijk", "youtube")
            == "abcdefghijk"
        )
        assert (
            extract_video_id("https://www.douyin.com/video/123456", "douyin")
            == "123456"
        )
        assert (
            extract_video_id(
                "https://www.kuaishou.com/short-video/3xabc?photoId=3xabc",
                "kuaishou",
            )
            == "3xabc"
        )


class TestDownloadDispatch:
    def test_unsupported_url_raises_without_network(self):
        with pytest.raises(DownloadError, match="暂不支持"):
            online.download_video("https://example.com/video.mp4")

    def test_missing_url_raises(self):
        with pytest.raises(DownloadError, match="未在输入中找到视频链接"):
            online.download_video("这里没有链接")


class TestFileHelpers:
    def test_existing_file_detected(self, tmp_path):
        video = tmp_path / "BV1xx.mp4"
        video.write_bytes(b"x")
        assert online._existing_file(tmp_path, "BV1xx") == video

    def test_empty_file_is_ignored(self, tmp_path):
        video = tmp_path / "BV1xx.mp4"
        video.write_bytes(b"")
        assert online._existing_file(tmp_path, "BV1xx") is None

    def test_netscape_cookie_file_contains_domain(self):
        path = online._write_netscape_cookie_file(
            {"SESSDATA": "abc"}, ".bilibili.com"
        )
        try:
            content = Path(path).read_text()
            assert "# Netscape HTTP Cookie File" in content
            assert ".bilibili.com\tTRUE\t/\tFALSE\t" in content
            assert "SESSDATA\tabc" in content
        finally:
            Path(path).unlink(missing_ok=True)


class TestYtdlpFallback:
    def test_locate_file_after_download(self, tmp_path, monkeypatch):
        video = tmp_path / "abc123.mp4"
        video.write_bytes(b"x")

        fake_info = {
            "id": "abc123",
            "title": "Tutorial",
            "duration": 10,
            "ext": "mp4",
        }

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=True):
                assert download is True
                return fake_info

        monkeypatch.setattr(online.yt_dlp, "YoutubeDL", FakeYDL)
        result = online._download_with_ytdlp(
            "https://youtube.com/watch?v=abc123", "youtube", tmp_path
        )
        assert result.video_path == video
        assert result.platform == "youtube"
        assert result.title == "Tutorial"
