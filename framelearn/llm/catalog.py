"""Static provider and model catalogs for FrameLearn's unified LLM layer.

The structure mirrors the cc-switch project:

- ``piModelCatalog`` tracks per-model capabilities (name, input modalities,
  context window, max output tokens).
- ``piProviderPresets`` tracks per-provider endpoints and wire formats.

FrameLearn deliberately supports only non-Responses wire formats:
``openai_chat`` (OpenAI Chat Completions), ``anthropic`` (Anthropic
Messages) and ``gemini`` (Gemini generateContent).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ApiFormat = Literal["openai_chat", "anthropic", "gemini"]
LlmPurpose = Literal["text", "vision"]


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    """A built-in provider definition.

    ``key`` is the canonical value accepted by ``settings.toml [text].provider``
    and ``[vision].vision_provider``. ``api_format`` is the only information
    used to pick a request builder; FrameLearn never selects a Responses
    builder from these presets.
    """

    key: str
    name: str
    api_format: ApiFormat
    base_url: str
    api_key_url: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Known capabilities for a model id.

    ``input`` mirrors cc-switch's ``PiModelInput[]``: ``"text"`` and/or
    ``"image"``. Unknown models are deliberately absent so the factory can
    pass them through for custom endpoints.
    """

    name: str
    vendor: str
    input: tuple[str, ...]
    reasoning: bool = False
    context_window: int | None = None
    max_tokens: int | None = None

    @property
    def supports_text(self) -> bool:
        return "text" in self.input

    @property
    def supports_images(self) -> bool:
        return "image" in self.input


# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        key="deepseek",
        name="DeepSeek",
        api_format="openai_chat",
        base_url="https://api.deepseek.com/v1/",
        api_key_url="https://platform.deepseek.com/api_keys",
    ),
    "minimax": ProviderPreset(
        key="minimax",
        name="MiniMax",
        api_format="anthropic",
        base_url="https://api.minimaxi.com/anthropic",
        api_key_url="https://platform.minimaxi.com/user-center/basic-information/interface-key",
        aliases=("minimaxi", "minimax-anthropic"),
    ),
    "claude": ProviderPreset(
        key="claude",
        name="Claude (Anthropic)",
        api_format="anthropic",
        base_url="https://api.anthropic.com",
        api_key_url="https://console.anthropic.com/settings/keys",
        aliases=("anthropic",),
    ),
    "openai": ProviderPreset(
        key="openai",
        name="OpenAI",
        api_format="openai_chat",
        base_url="https://api.openai.com/v1/",
        api_key_url="https://platform.openai.com/api-keys",
    ),
    "openrouter": ProviderPreset(
        key="openrouter",
        name="OpenRouter",
        api_format="openai_chat",
        base_url="https://openrouter.ai/api/v1/",
        api_key_url="https://openrouter.ai/settings/keys",
    ),
    "kimi": ProviderPreset(
        key="kimi",
        name="Moonshot (Kimi)",
        api_format="openai_chat",
        base_url="https://api.moonshot.cn/v1/",
        api_key_url="https://platform.moonshot.cn/console/api-keys",
        aliases=("moonshot",),
    ),
    "zhipu": ProviderPreset(
        key="zhipu",
        name="智谱 AI",
        api_format="openai_chat",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        api_key_url="https://open.bigmodel.cn/usercenter/apikeys",
        aliases=("bigmodel", "glm"),
    ),
    "siliconflow": ProviderPreset(
        key="siliconflow",
        name="SiliconFlow",
        api_format="openai_chat",
        base_url="https://api.siliconflow.cn/v1/",
        api_key_url="https://siliconflow.cn/",
        aliases=("silicon_flow",),
    ),
    "gemini": ProviderPreset(
        key="gemini",
        name="Google Gemini",
        api_format="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/",
        api_key_url="https://aistudio.google.com/apikey",
        aliases=("google",),
    ),
    "dashscope": ProviderPreset(
        key="dashscope",
        name="阿里云百炼 DashScope",
        api_format="openai_chat",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_url="https://bailian.console.aliyun.com/?apiKey=1",
        aliases=("qwen",),
    ),
}

_ALIAS_TO_KEY: dict[str, str] = {
    alias: preset.key
    for preset in PROVIDER_PRESETS.values()
    for alias in preset.aliases
}


def normalize_provider_key(provider: str) -> str:
    """Return the canonical provider key for a configured provider name."""
    value = provider.strip().lower()
    return _ALIAS_TO_KEY.get(value, value)


def get_provider_preset(provider: str) -> ProviderPreset | None:
    """Resolve a provider preset by canonical key or alias."""
    return PROVIDER_PRESETS.get(normalize_provider_key(provider))


def provider_display_names() -> list[str]:
    """All accepted provider values (canonical keys plus aliases)."""
    names = list(PROVIDER_PRESETS)
    names.extend(_ALIAS_TO_KEY)
    return sorted(dict.fromkeys(names))


# ---------------------------------------------------------------------------
# Model capabilities catalog
# ---------------------------------------------------------------------------

MODEL_CATALOG: dict[str, ModelCapabilities] = {
    # MiniMax. The current settings.toml uses provider="claude" with the
    # MiniMax Anthropic-compatible endpoint; the model itself is vision capable.
    "MiniMax-M3": ModelCapabilities(
        name="MiniMax-M3",
        vendor="minimax",
        input=("text", "image"),
        reasoning=True,
        context_window=1_000_000,
        max_tokens=128_000,
    ),
    "MiniMax-M2.7": ModelCapabilities(
        name="MiniMax-M2.7",
        vendor="minimax",
        input=("text",),
        reasoning=True,
        context_window=204_800,
        max_tokens=131_072,
    ),
    "MiniMax-Text-01": ModelCapabilities(
        name="MiniMax-Text-01",
        vendor="minimax",
        input=("text",),
        reasoning=False,
        context_window=1_000_000,
        max_tokens=128_000,
    ),
    # DeepSeek. DeepSeek's OpenAI-compatible endpoint is Chat Completions,
    # not the Responses API.
    "deepseek-chat": ModelCapabilities(
        name="DeepSeek Chat",
        vendor="deepseek",
        input=("text",),
        reasoning=False,
        context_window=128_000,
        max_tokens=8_192,
    ),
    "deepseek-reasoner": ModelCapabilities(
        name="DeepSeek Reasoner",
        vendor="deepseek",
        input=("text",),
        reasoning=True,
        context_window=128_000,
        max_tokens=64_000,
    ),
    # SiliconFlow-hosted Qwen VL models (current vision default provider).
    "Qwen/Qwen3-VL-8B-Instruct": ModelCapabilities(
        name="Qwen3-VL-8B-Instruct",
        vendor="siliconflow",
        input=("text", "image"),
        reasoning=False,
        context_window=262_144,
        max_tokens=16_384,
    ),
    "Qwen/Qwen3-VL-32B-Instruct": ModelCapabilities(
        name="Qwen3-VL-32B-Instruct",
        vendor="siliconflow",
        input=("text", "image"),
        reasoning=False,
        context_window=262_144,
        max_tokens=32_768,
    ),
    "Qwen/Qwen2.5-VL-72B-Instruct": ModelCapabilities(
        name="Qwen2.5-VL-72B-Instruct",
        vendor="siliconflow",
        input=("text", "image"),
        reasoning=False,
        context_window=131_072,
        max_tokens=16_384,
    ),
    # OpenAI.
    "gpt-4o": ModelCapabilities(
        name="GPT-4o",
        vendor="openai",
        input=("text", "image"),
        reasoning=False,
        context_window=128_000,
        max_tokens=16_384,
    ),
    "gpt-4o-mini": ModelCapabilities(
        name="GPT-4o mini",
        vendor="openai",
        input=("text", "image"),
        reasoning=False,
        context_window=128_000,
        max_tokens=16_384,
    ),
    "gpt-4.1": ModelCapabilities(
        name="GPT-4.1",
        vendor="openai",
        input=("text", "image"),
        reasoning=False,
        context_window=1_047_576,
        max_tokens=32_768,
    ),
    # Google Gemini.
    "gemini-2.5-flash": ModelCapabilities(
        name="Gemini 2.5 Flash",
        vendor="gemini",
        input=("text", "image"),
        reasoning=True,
        context_window=1_048_576,
        max_tokens=65_536,
    ),
    "gemini-2.5-pro": ModelCapabilities(
        name="Gemini 2.5 Pro",
        vendor="gemini",
        input=("text", "image"),
        reasoning=True,
        context_window=1_048_576,
        max_tokens=65_536,
    ),
    "gemini-3.6-flash": ModelCapabilities(
        name="Gemini 3.6 Flash",
        vendor="gemini",
        input=("text", "image"),
        reasoning=True,
        context_window=1_048_576,
        max_tokens=65_536,
    ),
    # Anthropic Claude.
    "claude-sonnet-4-5": ModelCapabilities(
        name="Claude Sonnet 4.5",
        vendor="claude",
        input=("text", "image"),
        reasoning=True,
        context_window=200_000,
        max_tokens=64_000,
    ),
    "claude-opus-4-5": ModelCapabilities(
        name="Claude Opus 4.5",
        vendor="claude",
        input=("text", "image"),
        reasoning=True,
        context_window=200_000,
        max_tokens=64_000,
    ),
    # Moonshot / Kimi.
    "kimi-k2-turbo-preview": ModelCapabilities(
        name="Kimi K2 Turbo",
        vendor="kimi",
        input=("text",),
        reasoning=True,
        context_window=256_000,
        max_tokens=32_768,
    ),
    "moonshot-v1-8k": ModelCapabilities(
        name="Moonshot v1 8k",
        vendor="kimi",
        input=("text",),
        reasoning=False,
        context_window=8_192,
        max_tokens=4_096,
    ),
    # Zhipu / GLM.
    "glm-4.5": ModelCapabilities(
        name="GLM-4.5",
        vendor="zhipu",
        input=("text",),
        reasoning=True,
        context_window=128_000,
        max_tokens=32_768,
    ),
    "glm-4.5v": ModelCapabilities(
        name="GLM-4.5V",
        vendor="zhipu",
        input=("text", "image"),
        reasoning=True,
        context_window=128_000,
        max_tokens=32_768,
    ),
    "glm-4v-plus": ModelCapabilities(
        name="GLM-4V-Plus",
        vendor="zhipu",
        input=("text", "image"),
        reasoning=False,
        context_window=8_192,
        max_tokens=4_096,
    ),
}

_MODEL_PREFIXES: tuple[tuple[str, ModelCapabilities], ...] = (
    # Prefix entries are intentionally conservative. They only cover
    # vendor/model families whose modality semantics are unambiguous.
    (
        "Qwen/Qwen3-VL",
        ModelCapabilities(
            name="Qwen3-VL (prefix)",
            vendor="siliconflow",
            input=("text", "image"),
            reasoning=False,
            context_window=262_144,
            max_tokens=32_768,
        ),
    ),
    (
        "Qwen/Qwen2.5-VL",
        ModelCapabilities(
            name="Qwen2.5-VL (prefix)",
            vendor="siliconflow",
            input=("text", "image"),
            reasoning=False,
            context_window=131_072,
            max_tokens=16_384,
        ),
    ),
    (
        "Qwen/Qwen3-Coder",
        ModelCapabilities(
            name="Qwen3 Coder (prefix)",
            vendor="siliconflow",
            input=("text",),
            reasoning=False,
            context_window=262_144,
            max_tokens=32_768,
        ),
    ),
    (
        "deepseek-",
        ModelCapabilities(
            name="DeepSeek (prefix)",
            vendor="deepseek",
            input=("text",),
            reasoning=True,
            context_window=128_000,
            max_tokens=64_000,
        ),
    ),
    (
        "gpt-4",
        ModelCapabilities(
            name="GPT-4 family (prefix)",
            vendor="openai",
            input=("text", "image"),
            reasoning=False,
            context_window=128_000,
            max_tokens=16_384,
        ),
    ),
    (
        "gpt-5",
        ModelCapabilities(
            name="GPT-5 family (prefix)",
            vendor="openai",
            input=("text", "image"),
            reasoning=True,
            context_window=272_000,
            max_tokens=128_000,
        ),
    ),
    (
        "gemini-",
        ModelCapabilities(
            name="Gemini family (prefix)",
            vendor="gemini",
            input=("text", "image"),
            reasoning=True,
            context_window=1_048_576,
            max_tokens=65_536,
        ),
    ),
    (
        "claude-",
        ModelCapabilities(
            name="Claude family (prefix)",
            vendor="claude",
            input=("text", "image"),
            reasoning=True,
            context_window=200_000,
            max_tokens=64_000,
        ),
    ),
)


def get_model_capabilities(model: str | None) -> ModelCapabilities | None:
    """Return capabilities for a model id, or ``None`` when unknown.

    Matching order:
      1. exact key
      2. case-insensitive exact key
      3. conservative known-family prefix
    """
    if not model:
        return None
    value = model.strip()
    if value in MODEL_CATALOG:
        return MODEL_CATALOG[value]

    lowered = value.lower()
    for key, capabilities in MODEL_CATALOG.items():
        if key.lower() == lowered:
            return capabilities

    lowered_model = value.lower()
    for prefix, capabilities in _MODEL_PREFIXES:
        if lowered_model.startswith(prefix.lower()):
            return capabilities
    return None


def provider_for_model(model: str | None) -> str | None:
    """Infer a canonical provider key from the model catalog, if known."""
    capabilities = get_model_capabilities(model)
    return capabilities.vendor if capabilities else None


def image_capable_models() -> list[str]:
    """Known model ids that accept image input."""
    return [key for key, cap in MODEL_CATALOG.items() if cap.supports_images]


def text_only_models() -> list[str]:
    """Known model ids that only accept text input."""
    return [key for key, cap in MODEL_CATALOG.items() if not cap.supports_images]
