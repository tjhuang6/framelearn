"""Fail-fast configuration validation.

The pipeline is expensive (ASR for hours of audio, dozens of LLM calls), so
configuration that can never work should be rejected before any processing
starts.  Checks here are intentionally offline / structural; actual auth
failures from providers are still fatal and are raised as
:class:`framelearn.errors.ConfigurationError` by ``provider_adapter``.
"""

from __future__ import annotations

import os
from pathlib import Path

from framelearn.config import get as config_get
from framelearn.errors import ConfigurationError
from framelearn.llm.catalog import get_model_capabilities
from framelearn.provider_adapter import (
    _validate_api_key,
    load_text_config,
    load_vision_config,
)


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"缺少必需的环境变量 {name}，请在 .env 中配置"
        )
    if value.startswith("your_"):
        raise ConfigurationError(
            f"{name} 还是占位符，请在 .env 中填入真实值"
        )
    return value


def _validate_model_max_tokens(
    model: str,
    setting_key: str,
    default: int,
    label: str,
) -> int:
    requested = int(config_get(setting_key, default))
    if requested <= 0:
        raise ConfigurationError(f"{label} 的 {setting_key} 必须大于 0")

    capabilities = get_model_capabilities(model)
    if (
        capabilities
        and capabilities.max_tokens
        and requested > capabilities.max_tokens
    ):
        raise ConfigurationError(
            f"{label}配置错误：{setting_key}={requested} 超过模型 "
            f"{model} 的最大输出 {capabilities.max_tokens}。"
            "请改小该值，或更换支持更大输出的模型。"
        )
    return requested


def validate_llm_config() -> None:
    """Validate text + vision model settings and API keys before running."""
    text_config = load_text_config()
    try:
        _validate_api_key(text_config)
    except ValueError as e:
        raise ConfigurationError(str(e)) from e

    from framelearn.llm import create_llm_client

    create_llm_client("text", config=text_config)
    _validate_model_max_tokens(
        text_config.model,
        "blog_gen.max_tokens",
        16384,
        "文本模型",
    )

    vision_config = load_vision_config()
    try:
        _validate_api_key(vision_config)
    except ValueError as e:
        raise ConfigurationError(str(e)) from e

    create_llm_client("vision", config=vision_config)
    _validate_model_max_tokens(
        vision_config.model,
        "blog_gen.vision_max_tokens",
        8192,
        "视觉模型",
    )


def validate_chunking_config() -> None:
    """Validate chunk/parallel settings that would fail after hours of work."""
    if float(config_get("chunking.segment_minutes", 10)) <= 0:
        raise ConfigurationError("chunking.segment_minutes 必须大于 0")
    if int(config_get("chunking.max_images_per_chunk", 20)) <= 0:
        raise ConfigurationError("chunking.max_images_per_chunk 必须大于 0")
    if int(config_get("chunking.concurrency", 5)) <= 0:
        raise ConfigurationError("chunking.concurrency 必须大于 0")

    mode = str(config_get("chunking.parallel_mode", "async")).strip().lower()
    if mode not in ("async", "asyncio", "process", "processes", "multiprocessing"):
        raise ConfigurationError(
            "chunking.parallel_mode 必须是 'async' 或 'process'，"
            f"当前值：{mode!r}"
        )

    if int(config_get("blog_gen.vision_batch_size", 8)) <= 0:
        raise ConfigurationError("blog_gen.vision_batch_size 必须大于 0")
    if int(config_get("blog_gen.max_retakes", 1)) < 0:
        raise ConfigurationError("blog_gen.max_retakes 不能小于 0")
    if int(config_get("blog_gen.max_calls", 3)) < 1:
        raise ConfigurationError("blog_gen.max_calls 必须大于等于 1")
    if int(config_get("blog_gen.vision_max_calls", 3)) < 1:
        raise ConfigurationError("blog_gen.vision_max_calls 必须大于等于 1")

    raw_enabled = config_get("blog_gen.dump_raw_responses", True)
    if not isinstance(raw_enabled, bool):
        raise ConfigurationError(
            "blog_gen.dump_raw_responses 必须是布尔值，"
            f"当前值：{raw_enabled!r}"
        )
    raw_on_success = config_get("blog_gen.dump_raw_on_success", False)
    if not isinstance(raw_on_success, bool):
        raise ConfigurationError(
            "blog_gen.dump_raw_on_success 必须是布尔值，"
            f"当前值：{raw_on_success!r}"
        )


def validate_asr_config() -> None:
    """Validate ASR settings only when audio transcription is actually needed."""
    provider = str(config_get("asr.provider", "")).strip().lower()
    model = str(config_get("asr.model", "")).strip()

    if provider == "dashscope":
        _require_env("DASHSCOPE_API_KEY")
        _require_env("OSS_ACCESS_KEY_ID")
        _require_env("OSS_ACCESS_KEY_SECRET")
        if not str(config_get("asr.oss.bucket", "")).strip():
            raise ConfigurationError(
                "asr.oss.bucket 未配置：DashScope ASR 需要 OSS 存储桶"
            )
        if not str(config_get("asr.oss.region", "")).strip():
            raise ConfigurationError("asr.oss.region 未配置")
        if model not in ("qwen-audio-3.0-asr-flash-filetrans", "paraformer-v2"):
            raise ConfigurationError(f"不支持的 DashScope ASR 模型：{model}")
        if int(config_get("asr.chunk_duration", 1800)) <= 0:
            raise ConfigurationError("asr.chunk_duration 必须大于 0")
        return

    if provider == "siliconflow":
        _require_env("SILICONFLOW_API_KEY")
        if not model:
            raise ConfigurationError("asr.model 未配置")
        return

    raise ConfigurationError(
        "asr.provider 必须是 'dashscope' 或 'siliconflow'，"
        f"当前值：{provider!r}"
    )


def _cookie_env(platform: str) -> str:
    for key in (
        f"{platform.upper()}_COOKIE",
        f"{platform.upper()}_COOKIE_FILE",
    ):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _parse_cookie_string(raw_cookie: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in raw_cookie.split(";"):
        if "=" in part:
            key, value = part.strip().split("=", 1)
            if key:
                cookies[key] = value
    return cookies


def validate_download_config(platform: str | None) -> None:
    """Validate optional platform cookie settings before starting a download.

    We cannot prove a cookie is accepted by the platform without a network
    call, but malformed values (a path that doesn't exist, a string with no
    ``name=value`` pairs, a Bilibili cookie missing SESSDATA) can be caught
    here immediately.
    """
    if platform not in ("bilibili", "youtube", "douyin", "kuaishou"):
        return

    raw = _cookie_env(platform)
    if not raw:
        if platform == "bilibili":
            sessdata = os.getenv("SESSDATA", "").strip()
            if sessdata.startswith("your_"):
                raise ConfigurationError(
                    "SESSDATA 还是占位符，请在 .env 中填入真实值"
                )
        return

    candidate = Path(raw).expanduser()
    if candidate.is_file():
        if platform == "bilibili":
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            if "SESSDATA" not in text:
                raise ConfigurationError(
                    f"{candidate} 不是有效的 Bilibili cookie 文件：缺少 SESSDATA"
                )
        return

    cookies = _parse_cookie_string(raw)
    if not cookies:
        raise ConfigurationError(
            f"{platform.upper()}_COOKIE 既不是存在的文件，也不是 "
            "`name=value; ...` 格式的 Cookie 字符串"
        )

    if platform == "bilibili" and "SESSDATA" not in cookies:
        raise ConfigurationError(
            "BILIBILI_COOKIE 中缺少 SESSDATA，B 站登录 Cookie 无效"
        )


def validate_run_config(
    *,
    platform: str | None = None,
    has_subtitle: bool = False,
) -> None:
    """Fail-fast checks for one ``run`` command.

    Text/vision config is always required by the anchored blog pipeline.
    ASR config is only required when no subtitle source is available.
    """
    validate_llm_config()
    validate_chunking_config()
    validate_download_config(platform)
    if not has_subtitle:
        validate_asr_config()
