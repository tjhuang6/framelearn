"""Unified LLM client and provider factory for FrameLearn.

Public API summary::

    from framelearn.llm import complete, complete_async, create_llm_client

    answer = complete("text", "解释一下深度学习")
    answer = complete("vision", "这张图适合做教材插图吗？", images=["frame.jpg"])

    client = create_llm_client("text")
    answer = client.complete("解释一下深度学习")
    answer = await client.complete_async("解释一下深度学习")

The factory resolves the concrete provider/model from the existing
``load_text_config()`` / ``load_vision_config()`` path (env overrides first,
then settings.toml) and validates known model capabilities. Request building
is delegated to :mod:`framelearn.provider_adapter`, whose provider table is
generated from :mod:`framelearn.llm.catalog` and contains no Responses wire
format.
"""

from __future__ import annotations

from framelearn.llm.catalog import (
    LlmPurpose,
    ModelCapabilities,
    ProviderPreset,
    get_model_capabilities,
    get_provider_preset,
    image_capable_models,
    normalize_provider_key,
    provider_display_names,
)
from framelearn.provider_adapter import (
    ProviderConfig,
    call_llm,
    call_llm_async,
    call_llm_async_interleaved,
    call_llm_with_tools,
    call_llm_with_tools_async,
    load_text_config,
    load_vision_config,
)


class LlmClient:
    """A concrete text or vision model client resolved by the factory.

    ``config`` is the fully-resolved provider configuration. The client keeps
    the original ``ProviderConfig`` so it can also be passed to any legacy
    ``provider_adapter`` function.
    """

    def __init__(
        self,
        config: ProviderConfig,
        purpose: LlmPurpose,
        capabilities: ModelCapabilities | None,
        preset: ProviderPreset | None = None,
    ) -> None:
        self.config = config
        self.purpose: LlmPurpose = purpose
        self.capabilities = capabilities
        self.preset = preset or get_provider_preset(config.provider)

    @property
    def provider(self) -> str:
        return self.config.provider

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def api_format(self) -> str | None:
        return self.preset.api_format if self.preset else None

    @property
    def supports_images(self) -> bool:
        """True for known image-capable models; unknown models pass through."""
        return bool(self.capabilities and self.capabilities.supports_images)

    # ------------------------------------------------------------------
    # Call surface
    # ------------------------------------------------------------------

    def complete(
        self,
        prompt: str,
        images: list[str] | None = None,
        max_tokens: int = 4096,
        timeout: int = 300,
    ) -> str:
        """Synchronous text/image completion."""
        return call_llm(
            prompt,
            self.config,
            images=images,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    async def complete_async(
        self,
        prompt: str,
        images: list[str] | None = None,
        max_tokens: int = 4096,
        timeout: int = 300,
    ) -> str:
        """Asynchronous text/image completion."""
        return await call_llm_async(
            prompt,
            self.config,
            images=images,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    async def complete_interleaved_async(
        self,
        segments: list[dict],
        max_tokens: int = 8192,
        timeout: int = 600,
    ) -> str:
        """Asynchronous vision call with text/image segments interleaved."""
        return await call_llm_async_interleaved(
            segments,
            self.config,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        images: list[str] | None = None,
        max_tokens: int = 512,
        timeout: int = 60,
    ) -> dict:
        """Synchronous tool-calling completion."""
        return call_llm_with_tools(
            messages,
            tools,
            self.config,
            images=images,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    async def complete_with_tools_async(
        self,
        messages: list[dict],
        tools: list[dict],
        images: list[str] | None = None,
        max_tokens: int = 512,
        timeout: int = 60,
    ) -> dict:
        """Asynchronous tool-calling completion."""
        return await call_llm_with_tools_async(
            messages,
            tools,
            self.config,
            images=images,
            max_tokens=max_tokens,
            timeout=timeout,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _validate_purpose(purpose: str) -> None:
    if purpose not in ("text", "vision"):
        raise ValueError(f"purpose must be 'text' or 'vision', got {purpose!r}")


def _require_base_url(provider: str, base_url: str | None, preset: ProviderPreset | None) -> str:
    if base_url:
        return base_url
    if preset:
        return preset.base_url
    raise ValueError(
        f"Missing base_url for provider '{provider}'. "
        "Add base_url to settings.toml or pass it to create_llm_client()."
    )


def _custom_preset(provider: str, base_url: str) -> ProviderPreset:
    """Synthesize an OpenAI Chat Completions preset for custom endpoints."""
    return ProviderPreset(
        key=provider,
        name=provider,
        api_format="openai_chat",
        base_url=base_url,
    )


def _validate_capabilities(
    purpose: LlmPurpose,
    model: str,
    capabilities: ModelCapabilities | None,
) -> None:
    if capabilities is None:
        # Unknown/custom model: pass through so custom endpoints keep working.
        return
    if purpose == "vision" and not capabilities.supports_images:
        candidates = ", ".join(image_capable_models()[:8])
        raise ValueError(
            f"Model '{model}' is known as text-only and cannot be used as a "
            "vision model. Choose an image-capable model, e.g.: "
            f"{candidates}"
        )
    if purpose == "text" and not capabilities.supports_text:
        raise ValueError(
            f"Model '{model}' is known as image-only and cannot be used as a "
            "text model."
        )


def _resolve_preset_or_custom(config: ProviderConfig) -> ProviderPreset | None:
    """Resolve a catalog preset, falling back to a synthetic chat preset.

    The fallback covers user-managed OpenAI-compatible endpoints that are not
    in the built-in catalog. It always uses ``openai_chat`` and therefore can
    never select the Responses API.
    """
    preset = get_provider_preset(config.provider)
    if preset is not None:
        return preset
    if config.base_url and config.api_key:
        return _custom_preset(config.provider, config.base_url)
    return None


def _apply_overrides(
    config: ProviderConfig,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    api_key: str | None,
) -> ProviderConfig:
    provider_key = config.provider
    if provider:
        provider_key = normalize_provider_key(provider)

    preset = get_provider_preset(provider_key)
    resolved_base_url = _require_base_url(
        provider_key,
        base_url or (config.base_url if provider_key == config.provider else None),
        preset,
    )
    return ProviderConfig(
        provider=provider_key,
        api_key=api_key if api_key is not None else config.api_key,
        model=model or config.model,
        base_url=resolved_base_url,
    )


def create_llm_client(
    purpose: LlmPurpose,
    *,
    config: ProviderConfig | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> LlmClient:
    """Create a concrete LLM client for ``"text"`` or ``"vision"``.

    Without a ``config`` argument the factory reads the same configuration
    sources as ``provider_adapter``: ``TEXT_*`` env vars for text and
    ``VISION_*`` env vars for vision, falling back to ``settings.toml``.

    Explicit ``provider`` / ``model`` / ``base_url`` / ``api_key`` arguments
    override the loaded config and are useful for tests and one-off calls.
    """
    _validate_purpose(purpose)

    if config is None:
        config = load_text_config() if purpose == "text" else load_vision_config()
    if provider or model or base_url or api_key is not None:
        config = _apply_overrides(config, provider, model, base_url, api_key)

    preset = _resolve_preset_or_custom(config)
    if preset is None:
        raise ValueError(
            f"Unknown provider '{config.provider}' and no explicit custom "
            "endpoint. Choose from: "
            f"{', '.join(provider_display_names())}, or set a full base_url "
            "and API key."
        )

    capabilities = get_model_capabilities(config.model)
    _validate_capabilities(purpose, config.model, capabilities)
    return LlmClient(config=config, purpose=purpose, capabilities=capabilities, preset=preset)


def get_text_client() -> LlmClient:
    """Factory shorthand for the configured text model."""
    return create_llm_client("text")


def get_vision_client() -> LlmClient:
    """Factory shorthand for the configured vision model."""
    return create_llm_client("vision")


# ---------------------------------------------------------------------------
# Module-level unified entry points
# ---------------------------------------------------------------------------


def complete(
    purpose: LlmPurpose,
    prompt: str,
    images: list[str] | None = None,
    max_tokens: int = 4096,
    timeout: int = 300,
    *,
    config: ProviderConfig | None = None,
) -> str:
    """Single synchronous entry point for both text and vision models.

    ``purpose="text"`` uses the [text] configuration; ``purpose="vision"``
    uses the [vision] configuration. ``images`` is normally provided for
    vision calls and ignored otherwise.
    """
    client = create_llm_client(purpose, config=config)
    return client.complete(
        prompt,
        images=images,
        max_tokens=max_tokens,
        timeout=timeout,
    )


async def complete_async(
    purpose: LlmPurpose,
    prompt: str,
    images: list[str] | None = None,
    max_tokens: int = 4096,
    timeout: int = 300,
    *,
    config: ProviderConfig | None = None,
) -> str:
    """Single asynchronous entry point for both text and vision models."""
    client = create_llm_client(purpose, config=config)
    return await client.complete_async(
        prompt,
        images=images,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def complete_text(
    prompt: str,
    max_tokens: int = 4096,
    timeout: int = 300,
    *,
    config: ProviderConfig | None = None,
) -> str:
    """Synchronous text-model convenience wrapper."""
    return complete(
        "text",
        prompt,
        max_tokens=max_tokens,
        timeout=timeout,
        config=config,
    )


async def complete_text_async(
    prompt: str,
    max_tokens: int = 4096,
    timeout: int = 300,
    *,
    config: ProviderConfig | None = None,
) -> str:
    """Asynchronous text-model convenience wrapper."""
    return await complete_async(
        "text",
        prompt,
        max_tokens=max_tokens,
        timeout=timeout,
        config=config,
    )


def complete_vision(
    prompt: str,
    images: list[str],
    max_tokens: int = 4096,
    timeout: int = 300,
    *,
    config: ProviderConfig | None = None,
) -> str:
    """Synchronous vision-model convenience wrapper."""
    return complete(
        "vision",
        prompt,
        images=images,
        max_tokens=max_tokens,
        timeout=timeout,
        config=config,
    )


async def complete_vision_async(
    prompt: str,
    images: list[str],
    max_tokens: int = 4096,
    timeout: int = 300,
    *,
    config: ProviderConfig | None = None,
) -> str:
    """Asynchronous vision-model convenience wrapper."""
    return await complete_async(
        "vision",
        prompt,
        images=images,
        max_tokens=max_tokens,
        timeout=timeout,
        config=config,
    )


__all__ = [
    "LlmClient",
    "create_llm_client",
    "get_text_client",
    "get_vision_client",
    "complete",
    "complete_async",
    "complete_text",
    "complete_text_async",
    "complete_vision",
    "complete_vision_async",
]
