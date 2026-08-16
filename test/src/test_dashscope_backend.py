"""Unit tests for the DashScope ASR backend.

Covers:
  - OssClient (upload / sign_url / delete, with mock oss2.Bucket)
  - DashscopeBackend._split_audio (chunk plan with mock FFmpeg)
  - DashscopeBackend._submit_task / _poll_task / _merge_results (mock httpx)
  - Time offset math in _merge_results
  - SRT generation via build_srt
"""

import json
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from framelearn.pipeline.asr_backends.dashscope import (
    AudioChunk,
    DashscopeBackend,
    _seconds_to_srt_time,
    build_srt,
)


# ------------------------------------------------------------------
# OssClient
# ------------------------------------------------------------------

class TestOssClient:
    """OssClient wraps oss2.Bucket. We mock oss2 entirely so the test
    runs offline and stays independent of Aliyun SDK behavior."""

    def _fake_oss2(self, mock_bucket):
        fake = MagicMock()
        fake.Auth = MagicMock(return_value="auth")
        fake.Bucket = MagicMock(return_value=mock_bucket)
        fake.resumable_upload = MagicMock()
        return fake

    def _patched_client(self, mock_bucket, bucket_name="my-bucket"):
        """Return a context manager that:
           - sets fake credentials in env
           - replaces config_get with a stub
           - replaces sys.modules['oss2'] with the fake
           Inside the with-block, `client` and `fake_oss2` are bound.
        """
        from framelearn.pipeline.asr_backends import oss_client
        from contextlib import contextmanager

        @contextmanager
        def ctx():
            fake_oss2 = self._fake_oss2(mock_bucket)

            def fake_config_get(key, default=""):
                return {
                    "asr.oss.bucket": bucket_name,
                    "asr.oss.region": "oss-cn-hangzhou",
                }.get(key, default)

            env_p = patch.dict(
                "os.environ",
                {
                    "OSS_ACCESS_KEY_ID": "real-key-id",
                    "OSS_ACCESS_KEY_SECRET": "real-key-secret",
                },
                clear=False,
            )
            cfg_p = patch(
                "framelearn.pipeline.asr_backends.oss_client.config_get",
                fake_config_get,
            )
            mod_p = patch.dict("sys.modules", {"oss2": fake_oss2})
            with env_p, cfg_p, mod_p:
                client = oss_client.OssClient()
                yield client, fake_oss2

        return ctx()

    def test_upload_uses_put_object_for_small_files(self, tmp_path):
        small = tmp_path / "small.m4a"
        small.write_bytes(b"x" * 100)
        mock_bucket = MagicMock()

        with self._patched_client(mock_bucket) as (client, _):
            result = client.upload(str(small), "audio/small.m4a")

        assert result == "audio/small.m4a"
        mock_bucket.put_object_from_file.assert_called_once_with(
            "audio/small.m4a", str(small)
        )

    def test_upload_uses_resumable_for_large_files(self, tmp_path):
        big = tmp_path / "big.m4a"
        # 11 MB > 10 MB multipart threshold
        big.write_bytes(b"x" * (11 * 1024 * 1024))
        mock_bucket = MagicMock()

        called = {"resumable": False, "single": False}

        with self._patched_client(mock_bucket) as (client, fake_oss2):
            def fake_resumable(bucket, key, path, part_size=None, num_threads=None):
                called["resumable"] = True
                return None

            fake_oss2.resumable_upload.side_effect = fake_resumable
            mock_bucket.put_object_from_file.side_effect = lambda k, p: called.update(
                {"single": True}
            )

            client.upload(str(big), "audio/big.m4a")

        assert called["resumable"] is True
        assert called["single"] is False

    def test_sign_url_returns_https(self):
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = (
            "https://my-bucket.oss-cn-hangzhou.aliyuncs.com/audio/test.m4a?Signature=xxx"
        )

        with self._patched_client(mock_bucket) as (client, _):
            url = client.sign_url("audio/test.m4a", 3600)

        assert url.startswith("https://")
        mock_bucket.sign_url.assert_called_once_with("GET", "audio/test.m4a", 3600)

    def test_delete_silently_ignores_errors(self):
        mock_bucket = MagicMock()
        mock_bucket.delete_object.side_effect = RuntimeError("network blip")

        with self._patched_client(mock_bucket) as (client, _):
            # Should not raise — cleanup is best-effort
            client.delete("audio/whatever.m4a")

    def test_constructor_rejects_placeholder_credentials(self, monkeypatch):
        from framelearn.pipeline.asr_backends import oss_client

        monkeypatch.setenv("OSS_ACCESS_KEY_ID", "your_key_id")
        monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "real-secret")
        with patch.dict("sys.modules", {"oss2": MagicMock()}):
            with pytest.raises(ValueError, match="OSS_ACCESS_KEY_ID"):
                oss_client.OssClient()

    def test_constructor_rejects_missing_bucket(self, monkeypatch):
        from framelearn.pipeline.asr_backends import oss_client

        monkeypatch.setenv("OSS_ACCESS_KEY_ID", "real-id")
        monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "real-secret")

        def fake_config_get(key, default=""):
            return {
                "asr.oss.bucket": "",
                "asr.oss.region": "oss-cn-hangzhou",
            }.get(key, default)

        with patch("framelearn.pipeline.asr_backends.oss_client.config_get", fake_config_get):
            with patch.dict("sys.modules", {"oss2": MagicMock()}):
                with pytest.raises(ValueError, match="asr.oss.bucket"):
                    oss_client.OssClient()


# ------------------------------------------------------------------
# AudioChunker / split_audio
# ------------------------------------------------------------------

class TestAudioChunker:
    """Verify the split plan: given a total duration and chunk_duration,
    split_audio should produce a list of AudioChunk with non-overlapping,
    chronologically ordered start_sec values."""

    def test_split_90_minute_audio_into_3_chunks(self, tmp_path, monkeypatch):
        audio = tmp_path / "lecture.m4a"
        audio.write_bytes(b"\x00")

        monkeypatch.setenv("DASHSCOPE_API_KEY", "real-key")

        # Mock ffprobe to report 90 min = 5400s
        ffprobe_output = "5400.0\n"

        backend = DashscopeBackend(api_key="real-key")
        backend.chunk_duration = 1800  # 30 min

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=ffprobe_output, returncode=0, stderr="")
            # Skip actual ffmpeg slicing by pre-creating the chunk files
            for i in range(3):
                (tmp_path / f"chunk_{i:03d}.m4a").write_bytes(b"\x00")

            chunks = backend._split_audio(audio, tmp_path)

        assert len(chunks) == 3
        assert chunks[0].start_sec == 0
        assert chunks[1].start_sec == 1800
        assert chunks[2].start_sec == 3600
        # last chunk is partial: 5400 - 3600 = 1800
        assert chunks[2].duration_sec == 1800

    def test_short_audio_yields_one_chunk(self, tmp_path, monkeypatch):
        audio = tmp_path / "short.m4a"
        audio.write_bytes(b"\x00")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "real-key")

        backend = DashscopeBackend(api_key="real-key")
        backend.chunk_duration = 1800

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="600.0\n", returncode=0, stderr="")
            (tmp_path / "chunk_000.m4a").write_bytes(b"\x00")
            chunks = backend._split_audio(audio, tmp_path)

        assert len(chunks) == 1
        assert chunks[0].start_sec == 0
        assert chunks[0].duration_sec == 600

    def test_split_chunks_are_sequential(self, tmp_path, monkeypatch):
        """Chunks must be in order and non-overlapping."""
        audio = tmp_path / "audio.m4a"
        audio.write_bytes(b"\x00")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "real-key")

        backend = DashscopeBackend(api_key="real-key")
        backend.chunk_duration = 600  # 10 min

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="1500.0\n", returncode=0, stderr="")
            for i in range(3):
                (tmp_path / f"chunk_{i:03d}.m4a").write_bytes(b"\x00")
            chunks = backend._split_audio(audio, tmp_path)

        starts = [c.start_sec for c in chunks]
        assert starts == sorted(starts)
        for i in range(len(chunks) - 1):
            assert chunks[i].start_sec + chunks[i].duration_sec == chunks[i + 1].start_sec


# ------------------------------------------------------------------
# DashscopeBackend API + polling + merge
# ------------------------------------------------------------------

class TestDashscopeBackend:
    """The high-level backend test focuses on:
       - _submit_task returns a task_id from the response
       - _poll_task retries on 5xx, succeeds when status=SUCCEEDED
       - _download_result fetches the transcription_url result
    """

    def _backend(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "real-key")
        return DashscopeBackend(api_key="real-key")

    def test_submit_task_returns_task_id(self, monkeypatch):
        backend = self._backend(monkeypatch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value = Mock(
                status_code=200,
                json=lambda: {
                    "output": {"task_id": "abc-123"},
                    "request_id": "req-1",
                },
            )
            task_id = backend._submit_task("https://example.com/audio.m4a?Signature=xxx")

        assert task_id == "abc-123"
        # Verify async mode was requested
        kwargs = mock_post.call_args.kwargs
        assert kwargs["headers"]["X-DashScope-Async"] == "enable"

    def test_submit_task_raises_when_response_missing_task_id(self, monkeypatch):
        backend = self._backend(monkeypatch)
        with patch("httpx.post") as mock_post:
            mock_post.return_value = Mock(
                status_code=200,
                json=lambda: {"output": {}, "request_id": "req-1"},
            )
            with pytest.raises(RuntimeError, match="task_id"):
                backend._submit_task("https://example.com/audio.m4a?Signature=xxx")

    def test_poll_task_succeeds_on_first_try(self, monkeypatch):
        backend = self._backend(monkeypatch)
        backend.poll_timeout = 60
        backend.poll_interval = 0  # don't actually sleep in tests

        with patch("httpx.get") as mock_get, \
             patch.object(backend, "_download_result") as mock_dl:
            mock_get.return_value = Mock(
                status_code=200,
                json=lambda: {
                    "output": {
                        "task_status": "SUCCEEDED",
                        "results": [{"transcription_url": "https://result.example.com/r"}],
                    }
                },
            )
            mock_dl.return_value = {"transcripts": [{"sentences": []}]}

            result = backend._poll_task("task-1")

        assert "transcripts" in result

    def test_poll_task_raises_on_failed_status(self, monkeypatch):
        backend = self._backend(monkeypatch)
        backend.poll_timeout = 60
        backend.poll_interval = 0

        with patch("httpx.get") as mock_get:
            mock_get.return_value = Mock(
                status_code=200,
                json=lambda: {
                    "output": {
                        "task_status": "FAILED",
                        "message": "audio too noisy",
                    }
                },
            )
            with pytest.raises(RuntimeError, match="失败"):
                backend._poll_task("task-1")

    def test_poll_task_retries_on_5xx(self, monkeypatch):
        backend = self._backend(monkeypatch)
        backend.poll_timeout = 60
        backend.poll_interval = 0

        with patch("httpx.get") as mock_get, \
             patch.object(backend, "_download_result") as mock_dl, \
             patch("time.sleep"):
            mock_get.side_effect = [
                Mock(status_code=500, json=lambda: {}),
                Mock(
                    status_code=200,
                    json=lambda: {
                        "output": {
                            "task_status": "SUCCEEDED",
                            "results": [{"transcription_url": "https://x.example/r"}],
                        }
                    },
                ),
            ]
            mock_dl.return_value = {"transcripts": [{"sentences": []}]}
            result = backend._poll_task("task-1")

        assert mock_get.call_count == 2
        assert "transcripts" in result

    def test_poll_task_times_out(self, monkeypatch):
        backend = self._backend(monkeypatch)
        backend.poll_timeout = 0.1
        backend.poll_interval = 0.05

        with patch("httpx.get") as mock_get, \
             patch("time.sleep"):
            mock_get.return_value = Mock(
                status_code=200,
                json=lambda: {"output": {"task_status": "PENDING"}},
            )
            with pytest.raises(TimeoutError):
                backend._poll_task("task-1")


# ------------------------------------------------------------------
# Time offset (merge_results)
# ------------------------------------------------------------------

class TestTimeOffset:
    """When chunk N starts at chunk.start_sec, every sentence inside that
    chunk's transcript must be shifted forward by exactly that amount."""

    def test_offset_applied_per_chunk(self):
        backend = DashscopeBackend(api_key="real-key")
        chunk0 = AudioChunk(index=0, path=Path("/tmp/c0.m4a"), start_sec=0, duration_sec=1800)
        chunk1 = AudioChunk(index=1, path=Path("/tmp/c1.m4a"), start_sec=1800, duration_sec=1800)

        # raw result has begin_time/end_time in milliseconds
        raw0 = {
            "transcripts": [
                {
                    "sentences": [
                        {"text": "hello", "begin_time": 1000, "end_time": 2000},
                    ]
                }
            ]
        }
        raw1 = {
            "transcripts": [
                {
                    "sentences": [
                        {"text": "world", "begin_time": 5000, "end_time": 7000},
                    ]
                }
            ]
        }

        segments = backend._merge_results([(chunk0, raw0), (chunk1, raw1)])

        assert len(segments) == 2
        # chunk 0: 1000ms → 1.0s, no offset
        assert segments[0].start == 1.0
        assert segments[0].end == 2.0
        assert segments[0].text == "hello"
        # chunk 1: 5000ms → 5.0s + 1800s chunk offset = 1805.0s
        assert segments[1].start == 1805.0
        assert segments[1].end == 1807.0
        assert segments[1].text == "world"

    def test_empty_transcripts_are_skipped(self):
        backend = DashscopeBackend(api_key="real-key")
        chunk = AudioChunk(index=0, path=Path("/tmp/c.m4a"), start_sec=0, duration_sec=1800)
        raw = {"transcripts": [{"sentences": []}]}
        assert backend._merge_results([(chunk, raw)]) == []

    def test_empty_text_sentences_are_skipped(self):
        backend = DashscopeBackend(api_key="real-key")
        chunk = AudioChunk(index=0, path=Path("/tmp/c.m4a"), start_sec=0, duration_sec=1800)
        raw = {
            "transcripts": [
                {
                    "sentences": [
                        {"text": "  ", "begin_time": 0, "end_time": 1000},
                        {"text": "real", "begin_time": 1000, "end_time": 2000},
                    ]
                }
            ]
        }
        segments = backend._merge_results([(chunk, raw)])
        assert len(segments) == 1
        assert segments[0].text == "real"


# ------------------------------------------------------------------
# SRT generation
# ------------------------------------------------------------------

class TestSrtGeneration:
    def test_seconds_to_srt_time_zero(self):
        assert _seconds_to_srt_time(0) == "00:00:00,000"

    def test_seconds_to_srt_time_with_milliseconds(self):
        assert _seconds_to_srt_time(1.5) == "00:00:01,500"
        assert _seconds_to_srt_time(61.25) == "00:01:01,250"
        assert _seconds_to_srt_time(3661.999) == "01:01:01,999"

    def test_build_srt_basic(self):
        segments = [
            Mock(start=0.0, end=2.5, text="第一句"),
            Mock(start=2.5, end=5.0, text="第二句"),
        ]
        srt = build_srt(segments)

        # Two blocks separated by blank line
        assert "1\n00:00:00,000 --> 00:00:02,500\n第一句" in srt
        assert "2\n00:00:02,500 --> 00:00:05,000\n第二句" in srt
        assert "\n\n" in srt

    def test_build_srt_skips_missing_timestamps(self):
        segments = [
            Mock(start=None, end=None, text="无时间戳应跳过"),
            Mock(start=0.0, end=1.0, text="有时间戳"),
        ]
        srt = build_srt(segments)
        assert "无时间戳应跳过" not in srt
        assert "有时间戳" in srt

    def test_build_srt_skips_empty_text(self):
        segments = [
            Mock(start=0.0, end=1.0, text=""),
            Mock(start=1.0, end=2.0, text="   "),
            Mock(start=2.0, end=3.0, text="非空"),
        ]
        srt = build_srt(segments)
        # Only the non-empty entry should remain
        assert srt.count("-->") == 1
        assert "非空" in srt
