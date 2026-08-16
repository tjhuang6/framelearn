"""Tests for the unified LLM provider factory (framelearn.llm)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from framelearn.llm import (
    complete,
    create_llm_client,
    get_model_capabilities,
    get_provider_preset,
)
from framelearn.llm.catalog import (
    MODEL_CATALOG,
    PROVIDER_PRESETS,
    provider_display_names,
)
from framelearn.provider_adapter import PROVIDERS, ProviderConfig


def _make_response(text: str = "ok") -> dict:
    return {"choices": [{"message": {"content": text}}]}


class TestCatalogs:
    def test_builtin_presets_only_use_non_responses_formats(self):
        allowed = {"openai_chat", "anthropic", "gemini"}
        for preset in PROVIDER_PRESETS.values():
            assert preset.api_format in allowed
            assert "/responses" not in preset.base_url

    def test_provider_adapter_providers_are_generated_from_presets(self):
        assert set(PROVIDERS) == set(PROVIDER_PRESETS)
        assert PROVIDERS["deepseek"]["type"] == "openai"
        assert PROVIDERS["minimax"]["type"] == "claude"
        assert PROVIDERS["gemini"]["type"] == "google"
        assert PROVIDERS["siliconflow"]["base_url"] == "https://api.siliconflow.cn/v1/"

    def test_provider_aliases_normalize_to_canonical_keys(self):
        assert get_provider_preset("minimaxi").key == "minimax"
        assert get_provider_preset("moonshot").key == "kimi"
        assert get_provider_preset("anthropic").key == "claude"
        assert get_provider_preset("qwen").key == "dashscope"

    def test_provider_display_names_include_aliases(self):
        names = provider_display_names()
        assert "minimax" in names
        assert "moonshot" in names
        assert "deepseek" in names

    def test_model_capabilities_exact_and_prefix(self):
        assert get_model_capabilities("MiniMax-M3").supports_images is True
        assert get_model_capabilities("deepseek-chat").supports_images is False
        assert get_model_capabilities("Qwen/Qwen3-VL-235B-A22B").supports_images is True
        assert get_model_capabilities("gpt-5.6-sol").supports_images is True

    def test_unknown_model_returns_none_for_passthrough(self):
        assert get_model_capabilities("my-company/custom-vl") is None


class TestFactory:
    def test_deepseek_text_client_uses_chat_completions(self):
        config = ProviderConfig(
            provider="deepseek",
            api_key="sk-test",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1/",
        )

        client = create_llm_client("text", config=config)

        assert client.purpose == "text"
        assert client.provider == "deepseek"
        assert client.api_format == "openai_chat"
        assert client.supports_images is False

        with patch("framelearn.provider_adapter.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=_make_response()),
            )
            assert client.complete("解释深度学习") == "ok"

        url = mock_post.call_args.args[0]
        assert url == "https://api.deepseek.com/v1/chat/completions"
        assert "/responses" not in url
        assert mock_post.call_args.kwargs["json"]["model"] == "deepseek-chat"

    def test_minimax_text_client_uses_anthropic_messages(self):
        config = ProviderConfig(
            provider="minimax",
            api_key="sk-minimax",
            model="MiniMax-M3",
            base_url="https://api.minimaxi.com/anthropic",
        )

        client = create_llm_client("text", config=config)

        assert client.api_format == "anthropic"
        assert client.supports_images is True

        with patch("framelearn.provider_adapter.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"content": [{"type": "text", "text": "ok"}]}),
            )
            assert client.complete("写一段笔记") == "ok"

        assert mock_post.call_args.args[0] == (
            "https://api.minimaxi.com/anthropic/v1/messages"
        )
        assert mock_post.call_args.kwargs["json"]["model"] == "MiniMax-M3"

    def test_factory_overrides_loaded_config(self):
        config = ProviderConfig(
            provider="claude",
            api_key="sk-old",
            model="old-model",
            base_url="https://api.anthropic.com",
        )
        client = create_llm_client(
            "text",
            config=config,
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1/",
            api_key="sk-new",
        )
        assert client.provider == "deepseek"
        assert client.model == "deepseek-chat"
        assert client.base_url == "https://api.deepseek.com/v1/"
        assert client.config.api_key == "sk-new"

    def test_known_text_only_model_rejected_for_vision(self):
        config = ProviderConfig(
            provider="deepseek",
            api_key="sk-test",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1/",
        )
        with pytest.raises(ValueError, match="text-only"):
            create_llm_client("vision", config=config)

    def test_unknown_model_and_custom_endpoint_are_allowed(self):
        config = ProviderConfig(
            provider="my-gateway",
            api_key="sk-custom",
            model="my-company/custom-vl",
            base_url="https://example.com/v1/",
        )
        client = create_llm_client("vision", config=config)
        assert client.capabilities is None
        assert client.supports_images is False
        assert client.api_format == "openai_chat"

    def test_invalid_purpose_raises(self):
        config = ProviderConfig(
            provider="deepseek",
            api_key="sk-test",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1/",
        )
        with pytest.raises(ValueError, match="purpose"):
            create_llm_client("image", config=config)  # type: ignore[arg-type]

    def test_tool_call_delegates_with_openai_chat_format(self):
        config = ProviderConfig(
            provider="siliconflow",
            api_key="sk-test",
            model="Qwen/Qwen3-VL-8B-Instruct",
            base_url="https://api.siliconflow.cn/v1/",
        )
        client = create_llm_client("vision", config=config)
        tools = [{"type": "function", "function": {"name": "decide", "parameters": {}}}]
        messages = [{"role": "user", "content": "看图"}]

        with patch("framelearn.provider_adapter.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"choices": [{"message": {"content": None}}]}),
            )
            client.complete_with_tools(messages, tools)

        assert mock_post.call_args.kwargs["json"]["tools"] == tools
        assert mock_post.call_args.kwargs["json"]["tool_choice"] == "required"


class TestUnifiedEntry:
    def test_complete_text_uses_loaded_text_config(self):
        config = ProviderConfig(
            provider="deepseek",
            api_key="sk-test",
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1/",
        )

        with patch("framelearn.llm.client.load_text_config", return_value=config):
            with patch("framelearn.provider_adapter.httpx.post") as mock_post:
                mock_post.return_value = MagicMock(
                    status_code=200,
                    json=MagicMock(return_value=_make_response("answer")),
                )
                assert complete("text", "问题") == "answer"

        assert mock_post.call_args.args[0].endswith("/chat/completions")

    def test_complete_vision_uses_loaded_vision_config(self):
        config = ProviderConfig(
            provider="siliconflow",
            api_key="sk-test",
            model="Qwen/Qwen3-VL-8B-Instruct",
            base_url="https://api.siliconflow.cn/v1/",
        )

        with patch("framelearn.llm.client.load_vision_config", return_value=config):
            with patch("framelearn.provider_adapter.httpx.post") as mock_post:
                mock_post.return_value = MagicMock(
                    status_code=200,
                    json=MagicMock(return_value=_make_response("keep")),
                )
                assert complete("vision", "验图") == "keep"

        sent_json = mock_post.call_args.kwargs["json"]
        assert sent_json["model"] == "Qwen/Qwen3-VL-8B-Instruct"
        assert mock_post.call_args.args[0].endswith("/chat/completions")

    def test_provider_adapter_async_convenience_entries_exist(self):
        from framelearn.provider_adapter import (
            call_text_llm_async,
            call_vision_llm_async,
        )

        assert callable(call_text_llm_async)
        assert callable(call_vision_llm_async)
