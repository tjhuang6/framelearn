"""LLM provider adapter for FrameLearn.

Inspired by Bilitato's providerAdapter.js — supports multiple providers
through a unified call_llm() interface. Internally branches on provider type:
  - "google"  → Gemini REST API
  - "claude"  → Anthropic Messages API
  - "openai"  → OpenAI-compatible API (default for DeepSeek, Kimi, etc.)
"""

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Provider definitions (mirrors Bilitato PROVIDERS object)
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, dict] = {
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/",
        "default_model": "gemini-2.0-flash",
        "type": "google",
        "reg_url": "https://aistudio.google.com/apikey",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1/",
        "default_model": "deepseek-v4-flash",   # v4-flash: 便宜快速；v4-pro: 最强；r1-0528: 深度推理
        "type": "openai",
        "reg_url": "https://platform.deepseek.com/api_keys",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1/",
        "default_model": "gpt-4.1-mini",
        "type": "openai",
        "reg_url": "https://platform.openai.com/api-keys",
    },
    "claude": {
        "name": "Claude (Anthropic)",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-20250514",
        "type": "claude",
        "reg_url": "https://console.anthropic.com/settings/keys",
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1/",
        "default_model": "openrouter/auto",
        "type": "openai",
        "reg_url": "https://openrouter.ai/settings/keys",
    },
    "kimi": {
        "name": "Moonshot (Kimi)",
        "base_url": "https://api.moonshot.cn/v1/",
        "default_model": "moonshot-v1-8k",
        "type": "openai",
        "reg_url": "https://platform.moonshot.cn/console/api-keys",
    },
    "zhipu": {
        "name": "智谱 AI",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "default_model": "glm-4-flash",
        "type": "openai",
        "reg_url": "https://open.bigmodel.cn/usercenter/apikeys",
    },
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    """Runtime provider configuration loaded from .env."""
    provider: str       # e.g. "deepseek"
    api_key: str
    model: str          # e.g. "deepseek-chat"
    base_url: str       # can be overridden for custom endpoints


def load_text_config() -> ProviderConfig:
    """Load text model config from environment variables."""
    provider_key = os.getenv("TEXT_PROVIDER", "deepseek")
    provider = PROVIDERS.get(provider_key)
    if not provider:
        raise ValueError(
            f"Unknown TEXT_PROVIDER: '{provider_key}'. "
            f"Choose from: {', '.join(PROVIDERS)}"
        )
    return ProviderConfig(
        provider=provider_key,
        api_key=os.getenv("TEXT_API_KEY", ""),
        model=os.getenv("TEXT_MODEL", provider["default_model"]),
        base_url=os.getenv("TEXT_BASE_URL", provider["base_url"]),
    )


def load_vision_config() -> ProviderConfig:
    """Load vision model config from environment variables."""
    provider_key = os.getenv("VISION_PROVIDER", "gemini")
    provider = PROVIDERS.get(provider_key)
    if not provider:
        raise ValueError(
            f"Unknown VISION_PROVIDER: '{provider_key}'. "
            f"Choose from: {', '.join(PROVIDERS)}"
        )
    return ProviderConfig(
        provider=provider_key,
        api_key=os.getenv("VISION_API_KEY", ""),
        model=os.getenv("VISION_MODEL", provider["default_model"]),
        base_url=os.getenv("VISION_BASE_URL", provider["base_url"]),
    )


# ---------------------------------------------------------------------------
# Image encoding helper
# ---------------------------------------------------------------------------

def encode_image(image_path: str) -> tuple[str, str]:
    """
    Encode a local image file to base64.

    Returns:
        (base64_data, mime_type) tuple
    """
    path = Path(image_path)
    suffix = path.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    mime_type = mime_map.get(suffix, "image/jpeg")
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, mime_type


# ---------------------------------------------------------------------------
# Request builders (one per provider type)
# ---------------------------------------------------------------------------

def _build_openai_request(
    config: ProviderConfig,
    prompt: str,
    images: Optional[list[str]] = None,
    max_tokens: int = 4096,
) -> tuple[str, dict, dict]:
    """Build OpenAI-compatible request (DeepSeek, Kimi, etc.)."""
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    # Build message content
    if images:
        content: list = [{"type": "text", "text": prompt}]
        for img_path in images:
            b64, mime = encode_image(img_path)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        messages = [{"role": "user", "content": content}]
    else:
        messages = [{"role": "user", "content": prompt}]

    body = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    return url, headers, body


def _build_gemini_request(
    config: ProviderConfig,
    prompt: str,
    images: Optional[list[str]] = None,
    max_tokens: int = 4096,
) -> tuple[str, dict, dict]:
    """Build Google Gemini REST request."""
    url = (
        f"{config.base_url.rstrip('/')}/"
        f"models/{config.model}:generateContent"
        f"?key={config.api_key}"
    )
    headers = {"Content-Type": "application/json"}

    # Build parts
    parts: list = []
    if images:
        for img_path in images:
            b64, mime = encode_image(img_path)
            parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    parts.append({"text": prompt})

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": max_tokens,
        },
    }
    return url, headers, body


def _build_claude_request(
    config: ProviderConfig,
    prompt: str,
    images: Optional[list[str]] = None,
    max_tokens: int = 4096,
) -> tuple[str, dict, dict]:
    """Build Anthropic Claude request."""
    url = f"{config.base_url.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": config.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    # Build message content
    if images:
        content: list = []
        for img_path in images:
            b64, mime = encode_image(img_path)
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            })
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]
    else:
        messages = [{"role": "user", "content": prompt}]

    body = {
        "model": config.model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    return url, headers, body


# ---------------------------------------------------------------------------
# Response parsers (one per provider type)
# ---------------------------------------------------------------------------

def _parse_openai_response(data: dict) -> str:
    return data["choices"][0]["message"]["content"]


def _parse_gemini_response(data: dict) -> str:
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _parse_claude_response(data: dict) -> str:
    return data["content"][0]["text"]


# ---------------------------------------------------------------------------
# Unified call interface
# ---------------------------------------------------------------------------

def call_llm(
    prompt: str,
    config: ProviderConfig,
    images: Optional[list[str]] = None,
    max_tokens: int = 4096,
    timeout: int = 30,
) -> str:
    """
    Unified LLM call interface.

    Args:
        prompt: Text prompt
        config: Provider configuration (from load_text_config or load_vision_config)
        images: Optional list of local image file paths (for vision tasks)
        max_tokens: Maximum tokens in the response
        timeout: Request timeout in seconds

    Returns:
        LLM response text

    Raises:
        ValueError: If provider config is missing API key
        httpx.HTTPError: On network or HTTP errors
    """
    if not config.api_key:
        raise ValueError(
            f"Missing API key for provider '{config.provider}'. "
            f"Set {config.provider.upper()}_API_KEY in .env"
        )

    provider_def = PROVIDERS.get(config.provider, {})
    provider_type = provider_def.get("type", "openai")

    # Build request based on provider type
    if provider_type == "google":
        url, headers, body = _build_gemini_request(config, prompt, images, max_tokens)
    elif provider_type == "claude":
        url, headers, body = _build_claude_request(config, prompt, images, max_tokens)
    else:
        url, headers, body = _build_openai_request(config, prompt, images, max_tokens)

    # Execute request
    response = httpx.post(url, headers=headers, json=body, timeout=timeout)

    if response.status_code != 200:
        raise httpx.HTTPStatusError(
            f"Provider '{config.provider}' returned {response.status_code}: {response.text}",
            request=response.request,
            response=response,
        )

    data = response.json()

    # Parse response based on provider type
    if provider_type == "google":
        return _parse_gemini_response(data)
    elif provider_type == "claude":
        return _parse_claude_response(data)
    else:
        return _parse_openai_response(data)


def call_text_llm(prompt: str, max_tokens: int = 4096) -> str:
    """Call text LLM using TEXT_PROVIDER config from .env."""
    config = load_text_config()
    return call_llm(prompt, config, max_tokens=max_tokens)


def call_vision_llm(
    prompt: str,
    images: list[str],
    max_tokens: int = 4096,
) -> str:
    """Call vision LLM using VISION_PROVIDER config from .env."""
    config = load_vision_config()
    return call_llm(prompt, config, images=images, max_tokens=max_tokens)
