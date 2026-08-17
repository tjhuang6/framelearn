from pathlib import Path

import pytest

import framelearn.preflight as preflight
from framelearn.errors import ConfigurationError


def test_max_tokens_above_model_limit_raises(monkeypatch):
    monkeypatch.setattr(
        preflight, "config_get", lambda key, default=None: 655360
    )
    with pytest.raises(ConfigurationError, match="最大输出"):
        preflight._validate_model_max_tokens(
            "MiniMax-M3", "blog_gen.max_tokens", 16384, "文本模型"
        )


def test_missing_dashscope_key_raises(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(
        preflight,
        "config_get",
        lambda key, default=None: {
            "asr.provider": "dashscope",
            "asr.model": "qwen-audio-3.0-asr-flash-filetrans",
            "asr.oss.bucket": "bucket",
            "asr.oss.region": "oss-cn-beijing",
            "asr.chunk_duration": 300,
        }.get(key, default),
    )
    with pytest.raises(ConfigurationError, match="DASHSCOPE_API_KEY"):
        preflight.validate_asr_config()


def test_malformed_bilibili_cookie_raises(monkeypatch):
    monkeypatch.setenv("BILIBILI_COOKIE", "not-a-cookie")
    with pytest.raises(ConfigurationError, match="Cookie"):
        preflight.validate_download_config("bilibili")


def test_dump_raw_responses_must_be_boolean(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "config_get",
        lambda key, default=None: "yes" if key == "blog_gen.dump_raw_responses" else (
            False if key == "blog_gen.dump_raw_on_success" else default
        ),
    )
    with pytest.raises(ConfigurationError, match="dump_raw_responses 必须是布尔值"):
        preflight.validate_chunking_config()


def test_dump_raw_on_success_must_be_boolean(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "config_get",
        lambda key, default=None: 1 if key == "blog_gen.dump_raw_on_success" else (
            True if key == "blog_gen.dump_raw_responses" else default
        ),
    )
    with pytest.raises(ConfigurationError, match="dump_raw_on_success 必须是布尔值"):
        preflight.validate_chunking_config()


def test_dump_raw_booleans_default_to_disabled_failure_only(monkeypatch):
    """Defaults: dump_raw_responses=True, dump_raw_on_success=False."""
    captured = {}

    def fake_get(key, default=None):
        captured[key] = default
        return default

    monkeypatch.setattr(preflight, "config_get", fake_get)
    # No exception raised.
    preflight.validate_chunking_config()
    assert captured["blog_gen.dump_raw_responses"] is True
    assert captured["blog_gen.dump_raw_on_success"] is False
