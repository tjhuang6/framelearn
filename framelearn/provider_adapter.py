"""LLM provider adapter for FrameLearn.

Inspired by Bilitato's providerAdapter.js — supports multiple providers
through a unified call_llm() interface. Internally branches on provider type:
  - "google"  → Gemini REST API
  - "claude"  → Anthropic Messages API
  - "openai"  → OpenAI-compatible API (default for DeepSeek, Kimi, etc.)
"""

import asyncio
import base64
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Provider definitions (mirrors Bilitato PROVIDERS object)
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, dict] = {
    # Per-provider metadata only — model names live in settings.toml,
    # not in code. base_url is the canonical endpoint (kept here so the
    # config loader can resolve a default without forcing users to copy
    # URLs into TOML); override via settings.toml when needed.
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/",
        "type": "google",
        "reg_url": "https://aistudio.google.com/apikey",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1/",
        "type": "openai",  # 用 chat/completions，不用 Responses API
        "reg_url": "https://platform.deepseek.com/api_keys",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1/",
        "type": "openai",
        "reg_url": "https://platform.openai.com/api-keys",
    },
    "claude": {
        "name": "Claude (Anthropic)",
        "base_url": "https://api.anthropic.com",
        "type": "claude",
        "reg_url": "https://console.anthropic.com/settings/keys",
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1/",
        "type": "openai",
        "reg_url": "https://openrouter.ai/settings/keys",
    },
    "kimi": {
        "name": "Moonshot (Kimi)",
        "base_url": "https://api.moonshot.cn/v1/",
        "type": "openai",
        "reg_url": "https://platform.moonshot.cn/console/api-keys",
    },
    "zhipu": {
        "name": "智谱 AI",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "type": "openai",
        "reg_url": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    "siliconflow": {
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1/",
        "type": "openai",
        "reg_url": "https://siliconflow.cn/",
    },
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    """Runtime provider configuration loaded from .env."""

    provider: str  # e.g. "deepseek"
    api_key: str
    model: str  # e.g. "deepseek-chat"
    base_url: str  # can be overridden for custom endpoints


def _required(key: str, label: str) -> str:
    """Read a required string from settings.toml; raise if missing/empty."""
    from framelearn.config import get as config_get
    value = config_get(key)
    if not value:
        raise ValueError(
            f"Missing required setting '{key}' (label: {label}). "
            f"Add it to settings.toml."
        )
    return str(value)


def _resolve_base_url(provider_key: str, toml_key: str, label: str) -> str:
    """Read base_url from TOML, falling back to PROVIDERS[provider].base_url.

    Raises if neither source provides one — we never silently fall back to
    a guessed endpoint.
    """
    from framelearn.config import get as config_get
    toml_value = config_get(toml_key)
    if toml_value:
        return str(toml_value)
    provider = PROVIDERS.get(provider_key, {})
    base_url = provider.get("base_url", "")
    if not base_url:
        raise ValueError(
            f"Missing required setting '{toml_key}' (label: {label}) and "
            f"provider '{provider_key}' has no built-in base_url. "
            f"Add '{toml_key} = \"...\"' to settings.toml."
        )
    return base_url


def _resolve_api_key(provider_key: str) -> str:
    """Read the API key for a provider from .env. No defaults — placeholder
    strings are caught later by call_llm, but a missing key fails fast here."""
    # Try provider-specific env var first (DEEPSEEK_API_KEY, etc.), then
    # the legacy generic names.
    candidates = [
        f"{provider_key.upper()}_API_KEY",
        "TEXT_API_KEY",  # legacy
        "VISION_API_KEY",  # legacy
    ]
    for env_name in candidates:
        value = os.getenv(env_name, "")
        if value:
            return value
    raise ValueError(
        f"Missing API key for provider '{provider_key}'. "
        f"Set one of: {', '.join(candidates)} in .env"
    )


def load_text_config() -> ProviderConfig:
    """Load text model config from settings.toml.

    Required TOML keys (raises if missing):
      [text]
      provider = "<name>"
      model    = "<model_id>"

    Optional TOML keys:
      [text]
      base_url = "..."   # falls back to PROVIDERS[provider].base_url

    API key is read from .env (DEEPSEEK_API_KEY / TEXT_API_KEY).
    """
    provider_key = _required("text.provider", "text LLM provider")
    if provider_key not in PROVIDERS:
        raise ValueError(
            f"Unknown text.provider: '{provider_key}'. Choose from: {', '.join(PROVIDERS)}"
        )
    return ProviderConfig(
        provider=provider_key,
        api_key=_resolve_api_key(provider_key),
        model=_required("text.model", "text LLM model"),
        base_url=_resolve_base_url(provider_key, "text.base_url", "text LLM base URL"),
    )


def load_vision_config() -> ProviderConfig:
    """Load vision model config from settings.toml.

    Required TOML keys (raises if missing):
      [vision]
      vision_provider = "<name>"
      vision_model    = "<model_id>"

    (Keys keep the ``vision_`` prefix because cache_manifest.py and the
    agent selectors already read them under that name.)

    Optional TOML keys:
      [vision]
      vision_base_url = "..."   # falls back to PROVIDERS[provider].base_url

    API key is read from .env (SILICONFLOW_API_KEY / VISION_API_KEY).
    """
    provider_key = _required("vision.vision_provider", "vision provider")
    if provider_key not in PROVIDERS:
        raise ValueError(
            f"Unknown vision.vision_provider: '{provider_key}'. "
            f"Choose from: {', '.join(PROVIDERS)}"
        )
    return ProviderConfig(
        provider=provider_key,
        api_key=_resolve_api_key(provider_key),
        model=_required("vision.vision_model", "vision model"),
        base_url=_resolve_base_url(
            provider_key, "vision.vision_base_url", "vision base URL"
        ),
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
    images: list[str] | None = None,
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
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )
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
    images: list[str] | None = None,
    max_tokens: int = 4096,
) -> tuple[str, dict, dict]:
    """Build Google Gemini REST request."""
    url = (
        f"{config.base_url.rstrip('/')}/models/{config.model}:generateContent?key={config.api_key}"
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
    images: list[str] | None = None,
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
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64},
                }
            )
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


def _build_responses_request(
    config: ProviderConfig,
    prompt: str,
    images: list[str] | None = None,
    max_tokens: int = 65536,
) -> tuple[str, dict, dict]:
    """Build OpenAI Responses API request (DeepSeek's Codex-compatible endpoint).

    Endpoint: POST {base_url}/responses
    Differences from chat/completions:
      - No /v1 suffix in base_url
      - Field is `input` (a single string or message list), not `messages`
      - Field is `max_output_tokens`, not `max_tokens`
      - Image attachments go in the input as content parts, not message parts
      - Response exposes a top-level `output_text` field for the assistant text
    """
    url = f"{config.base_url.rstrip('/')}/responses"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    if images:
        # Responses API input can be a list of content parts
        content: list = [{"type": "input_text", "text": prompt}]
        for img_path in images:
            b64, mime = encode_image(img_path)
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime};base64,{b64}",
                }
            )
        body_input: list | str = content
    else:
        body_input = prompt

    body = {
        "model": config.model,
        "input": body_input,
        "max_output_tokens": max_tokens,
        "temperature": 0.3,
    }
    return url, headers, body


def _parse_responses_response(data: dict) -> str:
    """Parse OpenAI Responses API response.

    The API returns both a structured `output` array and a convenience
    `output_text` field that concatenates all text parts. Prefer
    `output_text`; fall back to walking `output` if missing.
    """
    if "output_text" in data:
        return data["output_text"]
    # Fallback: walk the output array
    for item in data.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text"):
                    return part.get("text", "")
    raise ValueError(f"Responses API response missing text: {data}")


# ---------------------------------------------------------------------------
# Unified call interface
# ---------------------------------------------------------------------------


def _validate_api_key(config: ProviderConfig) -> None:
    """Raise ValueError if config.api_key is missing or looks like a placeholder."""
    if not config.api_key:
        raise ValueError(
            f"Missing API key for provider '{config.provider}'. "
            f"Set {config.provider.upper()}_API_KEY in .env"
        )
    if config.api_key.startswith("your_"):
        raise ValueError(
            f"Placeholder API key detected for provider '{config.provider}' "
            f"('{config.api_key}'). Replace it in .env with a real key from "
            f"{PROVIDERS.get(config.provider, {}).get('reg_url', 'the provider console')}."
        )


def _dispatch_sync(
    config: ProviderConfig,
    prompt: str,
    images: list[str] | None,
    max_tokens: int,
    timeout: int,
) -> str:
    """Build + send + parse a sync request; returns response text."""
    provider_def = PROVIDERS.get(config.provider, {})
    provider_type = provider_def.get("type", "openai")

    if provider_type == "google":
        url, headers, body = _build_gemini_request(config, prompt, images, max_tokens)
    elif provider_type == "claude":
        url, headers, body = _build_claude_request(config, prompt, images, max_tokens)
    elif provider_type == "responses":
        url, headers, body = _build_responses_request(config, prompt, images, max_tokens)
    else:
        url, headers, body = _build_openai_request(config, prompt, images, max_tokens)

    response = httpx.post(url, headers=headers, json=body, timeout=timeout)
    if response.status_code != 200:
        raise httpx.HTTPStatusError(
            f"Provider '{config.provider}' returned {response.status_code}: {response.text}",
            request=response.request,
            response=response,
        )

    data = response.json()
    if provider_type == "google":
        return _parse_gemini_response(data)
    elif provider_type == "claude":
        return _parse_claude_response(data)
    elif provider_type == "responses":
        return _parse_responses_response(data)
    return _parse_openai_response(data)


async def _dispatch_async(
    config: ProviderConfig,
    prompt: str,
    images: list[str] | None,
    max_tokens: int,
    timeout: int,
) -> str:
    """Async version of _dispatch_sync — uses httpx.AsyncClient."""
    provider_def = PROVIDERS.get(config.provider, {})
    provider_type = provider_def.get("type", "openai")

    if provider_type == "google":
        url, headers, body = _build_gemini_request(config, prompt, images, max_tokens)
    elif provider_type == "claude":
        url, headers, body = _build_claude_request(config, prompt, images, max_tokens)
    elif provider_type == "responses":
        url, headers, body = _build_responses_request(config, prompt, images, max_tokens)
    else:
        url, headers, body = _build_openai_request(config, prompt, images, max_tokens)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=body)
    if response.status_code != 200:
        raise httpx.HTTPStatusError(
            f"Provider '{config.provider}' returned {response.status_code}: {response.text}",
            request=response.request,
            response=response,
        )

    data = response.json()
    if provider_type == "google":
        return _parse_gemini_response(data)
    elif provider_type == "claude":
        return _parse_claude_response(data)
    elif provider_type == "responses":
        return _parse_responses_response(data)
    return _parse_openai_response(data)


def call_llm(
    prompt: str,
    config: ProviderConfig,
    images: list[str] | None = None,
    max_tokens: int = 65536,
    timeout: int = 300,
) -> str:
    """Sync LLM call. Thin wrapper that runs ``_dispatch_async`` in an event loop.

    Retained for callers that can't be migrated to async (e.g. the CLI command
    parser). For new code prefer ``await call_llm_async(...)``.
    """
    _validate_api_key(config)
    return asyncio.run(
        _dispatch_async(config, prompt, images, max_tokens, timeout)
    )


async def call_llm_async(
    prompt: str,
    config: ProviderConfig,
    images: list[str] | None = None,
    max_tokens: int = 65536,
    timeout: int = 300,
) -> str:
    """Async LLM call.

    Mirrors :func:`call_llm` but uses ``httpx.AsyncClient`` so multiple calls
    can run concurrently under a single event loop.

    Args:
        prompt: Text prompt.
        config: Provider configuration.
        images: Optional list of local image file paths (for vision tasks).
        max_tokens: Maximum tokens in the response.
        timeout: Per-request timeout in seconds.

    Returns:
        LLM response text.

    Raises:
        ValueError: If provider config is missing API key.
        httpx.HTTPError: On network or HTTP errors.
    """
    _validate_api_key(config)
    return await _dispatch_async(config, prompt, images, max_tokens, timeout)


def call_text_llm(prompt: str, max_tokens: int = 4096, timeout: int = 300) -> str:
    """Call text LLM using TEXT_PROVIDER config from .env."""
    config = load_text_config()
    return call_llm(prompt, config, max_tokens=max_tokens, timeout=timeout)


def call_vision_llm(
    prompt: str,
    images: list[str],
    max_tokens: int = 4096,
) -> str:
    """Call vision LLM using VISION_PROVIDER config from .env."""
    config = load_vision_config()
    return call_llm(prompt, config, images=images, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Tool-calling interface (OpenAI-compatible providers only)
# ---------------------------------------------------------------------------


def _inject_images_into_last_user_message(
    messages: list[dict],
    images: list[str],
) -> list[dict]:
    """Return a copy of messages with images injected into the last user turn."""
    msgs = [m.copy() for m in messages]
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            content = msgs[i].get("content", "")
            parts: list = (
                [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
            )
            for img_path in images:
                b64, mime = encode_image(img_path)
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                )
            msgs[i] = {**msgs[i], "content": parts}
            break
    return msgs


def _dispatch_tools_sync(
    config: ProviderConfig,
    messages: list[dict],
    tools: list[dict],
    images: list[str] | None,
    max_tokens: int,
    timeout: int,
) -> dict:
    """Sync core of call_llm_with_tools — validate, build, POST, return JSON."""
    provider_def = PROVIDERS.get(config.provider, {})
    provider_type = provider_def.get("type", "openai")

    if provider_type in ("google", "claude"):
        raise NotImplementedError(
            f"Tool calling is not implemented for provider type '{provider_type}'. "
            "Use an OpenAI-compatible provider (e.g. siliconflow, deepseek, kimi)."
        )

    msgs = _inject_images_into_last_user_message(messages, images) if images else messages

    url = f"{config.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.model,
        "messages": msgs,
        "tools": tools,
        "tool_choice": "required",
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }

    response = httpx.post(url, headers=headers, json=body, timeout=timeout)
    if response.status_code != 200:
        raise httpx.HTTPStatusError(
            f"Provider '{config.provider}' returned {response.status_code}: {response.text}",
            request=response.request,
            response=response,
        )
    return response.json()


async def _dispatch_tools_async(
    config: ProviderConfig,
    messages: list[dict],
    tools: list[dict],
    images: list[str] | None,
    max_tokens: int,
    timeout: int,
) -> dict:
    """Async version of _dispatch_tools_sync — uses httpx.AsyncClient."""
    provider_def = PROVIDERS.get(config.provider, {})
    provider_type = provider_def.get("type", "openai")

    if provider_type in ("google", "claude"):
        raise NotImplementedError(
            f"Tool calling is not implemented for provider type '{provider_type}'. "
            "Use an OpenAI-compatible provider (e.g. siliconflow, deepseek, kimi)."
        )

    msgs = _inject_images_into_last_user_message(messages, images) if images else messages

    url = f"{config.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.model,
        "messages": msgs,
        "tools": tools,
        "tool_choice": "required",
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=body)
    if response.status_code != 200:
        raise httpx.HTTPStatusError(
            f"Provider '{config.provider}' returned {response.status_code}: {response.text}",
            request=response.request,
            response=response,
        )
    return response.json()


def call_llm_with_tools(
    messages: list[dict],
    tools: list[dict],
    config: ProviderConfig,
    images: list[str] | None = None,
    max_tokens: int = 512,
    timeout: int = 60,
) -> dict:
    """Call an OpenAI-compatible LLM with tool definitions.

    Requires the model to respond with a tool call (tool_choice="required").
    Returns the raw response body; the caller parses tool_calls themselves.

    Args:
        messages: Full conversation history in OpenAI message format.
        tools: Tool definitions in OpenAI function-calling format
               (list of {"type": "function", "function": {...}}).
        config: Provider configuration.
        images: Optional image paths to inject into the last user message.
        max_tokens: Maximum tokens in the response.
        timeout: Request timeout in seconds.

    Returns:
        Raw JSON response body as a dict.

    Raises:
        NotImplementedError: For google or claude provider types.
        ValueError: If API key is missing.
        httpx.HTTPStatusError: On non-200 HTTP responses.
    """
    _validate_api_key(config)
    return _dispatch_tools_sync(
        config, messages, tools, images, max_tokens, timeout
    )


async def call_llm_with_tools_async(
    messages: list[dict],
    tools: list[dict],
    config: ProviderConfig,
    images: list[str] | None = None,
    max_tokens: int = 512,
    timeout: int = 60,
) -> dict:
    """Async tool-calling interface. See :func:`call_llm_with_tools` for args."""
    _validate_api_key(config)
    return await _dispatch_tools_async(
        config, messages, tools, images, max_tokens, timeout
    )
