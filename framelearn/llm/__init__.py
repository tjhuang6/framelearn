"""Unified text/vision LLM entry points and provider factory.

The catalog is imported eagerly because ``provider_adapter`` needs it at
import time. The client layer is exposed lazily to avoid an import cycle:
``llm.client`` imports ``provider_adapter``, while ``provider_adapter``
imports ``llm.catalog``.
"""

from __future__ import annotations

from framelearn.llm.catalog import (
    LlmPurpose,
    ModelCapabilities,
    ProviderPreset,
    get_model_capabilities,
    get_provider_preset,
    provider_display_names,
    provider_for_model,
)

_CLIENT_EXPORTS = {
    "LlmClient",
    "complete",
    "complete_async",
    "complete_text",
    "complete_text_async",
    "complete_vision",
    "complete_vision_async",
    "create_llm_client",
    "get_text_client",
    "get_vision_client",
}

__all__ = [
    "LlmClient",
    "LlmPurpose",
    "ModelCapabilities",
    "ProviderPreset",
    "complete",
    "complete_async",
    "complete_text",
    "complete_text_async",
    "complete_vision",
    "complete_vision_async",
    "create_llm_client",
    "get_model_capabilities",
    "get_provider_preset",
    "get_text_client",
    "get_vision_client",
    "provider_display_names",
    "provider_for_model",
]


def __getattr__(name: str):
    if name in _CLIENT_EXPORTS:
        from framelearn.llm import client as _client

        return getattr(_client, name)
    raise AttributeError(f"module 'framelearn.llm' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _CLIENT_EXPORTS)

