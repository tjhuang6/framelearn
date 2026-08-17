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
