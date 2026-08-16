"""Unit tests for CommandParser."""

from unittest.mock import patch

import pytest

from framelearn.command_parser import CommandParser

# ------------------------------------------------------------------
# Test fixtures
# ------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    """Force the rule-based parser path: no real provider / API key."""
    monkeypatch.delenv("TEXT_PROVIDER", raising=False)
    monkeypatch.delenv("TEXT_API_KEY", raising=False)
    return monkeypatch


@pytest.fixture
def with_provider(monkeypatch):
    """Set a real-looking TEXT_PROVIDER + TEXT_API_KEY to force the
    provider-based parse path."""
    monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("TEXT_API_KEY", "sk-real-key-that-is-long-enough")
    return monkeypatch


# ------------------------------------------------------------------
# Traditional commands
# ------------------------------------------------------------------


class TestTraditionalCommands:
    """Traditional command keywords should be returned verbatim, without
    invoking any LLM."""

    def test_run_command_passthrough(self, clean_env):
        parser = CommandParser()
        result = parser.parse('run "https://youtube.com/watch?v=xxx"')
        assert result == 'run "https://youtube.com/watch?v=xxx"'

    def test_run_command_with_local_file(self, clean_env):
        parser = CommandParser()
        result = parser.parse("run /Users/iwill/Downloads/video.mp4")
        assert result == "run /Users/iwill/Downloads/video.mp4"

    def test_ask_command_passthrough(self, clean_env):
        parser = CommandParser()
        result = parser.parse("ask 什么是装饰器")
        assert result == "ask 什么是装饰器"

    def test_summarize_command_passthrough(self, clean_env):
        parser = CommandParser()
        assert parser.parse("summarize") == "summarize"

    def test_help_command_passthrough(self, clean_env):
        parser = CommandParser()
        assert parser.parse("help") == "help"

    def test_traditional_does_not_invoke_provider(self, clean_env):
        """If the input starts with a traditional keyword, call_text_llm
        must not be called at all (no LLM cost on the common path)."""
        parser = CommandParser()
        with patch("framelearn.provider_adapter.call_text_llm") as mock_llm:
            parser.parse('run "https://example.com/video"')
            mock_llm.assert_not_called()


# ------------------------------------------------------------------
# Rule-based parsing (no API key configured)
# ------------------------------------------------------------------


class TestRuleBasedParsing:
    """When no LLM API key is configured, the parser falls back to rules."""

    def test_url_routes_to_run(self, clean_env):
        parser = CommandParser()
        result = parser.parse("帮我处理这个视频 https://bilibili.com/video/BV1xx")
        assert result.startswith("run https://")
        assert "BV1xx" in result

    def test_local_mp4_routes_to_run(self, clean_env):
        parser = CommandParser()
        result = parser.parse("处理这个本地视频 /Users/iwill/Downloads/lesson.mp4")
        assert result.startswith("run /")
        assert "lesson.mp4" in result

    def test_summarize_keyword_routes_to_summarize(self, clean_env):
        parser = CommandParser()
        assert parser.parse("总结一下我刚才学到的知识") == "summarize"

    def test_general_question_routes_to_ask(self, clean_env):
        parser = CommandParser()
        result = parser.parse("什么是 Python 装饰器")
        assert result.startswith("ask ")
        assert "Python" in result

    def test_video_intent_without_source_returns_error(self, clean_env):
        parser = CommandParser()
        with pytest.raises(ValueError, match="缺少视频链接或文件路径"):
            parser.parse("处理这个视频")


# ------------------------------------------------------------------
# _is_traditional_command
# ------------------------------------------------------------------


class TestIsTraditionalCommand:
    def test_run_keyword(self):
        assert CommandParser()._is_traditional_command("run video.mp4") is True

    def test_ask_keyword(self):
        assert CommandParser()._is_traditional_command("ask why") is True

    def test_summarize_keyword(self):
        assert CommandParser()._is_traditional_command("summarize") is True

    def test_help_keyword(self):
        assert CommandParser()._is_traditional_command("help me") is True

    def test_session_keyword(self):
        assert CommandParser()._is_traditional_command("session list") is True

    def test_natural_language_not_traditional(self):
        assert CommandParser()._is_traditional_command("帮我处理视频 https://...") is False

    def test_empty_string_not_traditional(self):
        assert CommandParser()._is_traditional_command("") is False

    def test_whitespace_only_not_traditional(self):
        assert CommandParser()._is_traditional_command("   ") is False


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


class TestErrorHandling:
    def test_error_prefix_raises_value_error(self, clean_env):
        """When the LLM (or rule engine) returns 'error: ...', the parser
        must surface it as a ValueError with the message."""
        parser = CommandParser()
        # Simulate the rule-based engine emitting an error: prefix
        with (
            patch.object(parser, "_parse_with_llm", return_value="error: 缺少视频链接或文件路径"),
            pytest.raises(ValueError, match="缺少视频链接或文件路径"),
        ):
            parser.parse("处理这个视频")

    def test_missing_url_raises(self, clean_env):
        """Natural language expressing video intent without a source
        must raise a clear error."""
        parser = CommandParser()
        with pytest.raises(ValueError, match="缺少视频链接或文件路径"):
            parser.parse("帮我处理这个视频")

    def test_error_message_does_not_include_error_prefix(self, clean_env):
        """The 'error:' prefix should be stripped from the raised
        message — the user should see '缺少视频...' not 'error: 缺少视频...'."""
        parser = CommandParser()
        with (
            patch.object(
                parser, "_parse_with_llm", return_value="error: 无法理解意图，请明确说明需求"
            ),
            pytest.raises(ValueError) as exc,
        ):
            parser.parse("做个饭")
        assert "error:" not in str(exc.value)
        assert "无法理解意图" in str(exc.value)


# ------------------------------------------------------------------
# Placeholder key detection (5s-timeout / no-LLM path)
# ------------------------------------------------------------------


class TestPlaceholderKeyDetection:
    """When the user *thinks* they've configured an API key but the value
    is still a placeholder, the parser must NOT try to call the API."""

    def test_placeholder_your_prefix_falls_back_to_rules(self, monkeypatch):
        monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
        monkeypatch.setenv("TEXT_API_KEY", "your_key_here")
        parser = CommandParser()
        # Should NOT raise — falls through to rule-based parsing
        result = parser.parse("什么是 Python")
        assert result.startswith("ask ")

    def test_placeholder_sk_xxx_falls_back_to_rules(self, monkeypatch):
        monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
        monkeypatch.setenv("TEXT_API_KEY", "sk-xxx")
        parser = CommandParser()
        result = parser.parse("什么是 Python")
        assert result.startswith("ask ")

    def test_too_short_key_falls_back_to_rules(self, monkeypatch):
        monkeypatch.setenv("TEXT_PROVIDER", "deepseek")
        monkeypatch.setenv("TEXT_API_KEY", "short")
        parser = CommandParser()
        result = parser.parse("什么是 Python")
        assert result.startswith("ask ")

    def test_valid_key_invokes_provider(self, with_provider):
        parser = CommandParser()
        # call_text_llm is imported lazily inside _parse_via_provider
        with patch("framelearn.provider_adapter.call_text_llm") as mock_llm:
            mock_llm.return_value = "ask 什么是 Python"
            parser.parse("什么是 Python")
            mock_llm.assert_called_once()


# ------------------------------------------------------------------
# Timeout / retry / debug
# ------------------------------------------------------------------


class TestTimeoutAndRetry:
    """The parser is on the critical path of every CLI invocation — it must
    fail fast and not silently hang the terminal."""

    def test_default_timeout_is_5_seconds(self, with_provider):
        parser = CommandParser()
        assert parser.timeout == 5

    def test_default_max_retries_is_one(self, with_provider):
        parser = CommandParser()
        assert parser.max_retries == 1

    def test_timeout_passed_to_provider(self, with_provider):
        parser = CommandParser()
        with patch("framelearn.provider_adapter.call_text_llm") as mock_llm:
            mock_llm.return_value = "ask x"
            parser.parse("什么是 x")
            kwargs = mock_llm.call_args.kwargs
            assert kwargs.get("timeout") == 5

    def test_custom_timeout_honored(self, with_provider):
        parser = CommandParser(timeout=2)
        with patch("framelearn.provider_adapter.call_text_llm") as mock_llm:
            mock_llm.return_value = "ask x"
            parser.parse("什么是 x")
            assert mock_llm.call_args.kwargs.get("timeout") == 2

    def test_transient_timeout_retries_then_succeeds(self, with_provider):
        import httpx

        parser = CommandParser()
        with patch("framelearn.provider_adapter.call_text_llm") as mock_llm:
            mock_llm.side_effect = [
                httpx.TimeoutException("slow"),
                "ask 什么是 Python",
            ]
            with patch("time.sleep"):  # don't actually sleep
                result = parser.parse("什么是 Python")
            assert result == "ask 什么是 Python"
            assert mock_llm.call_count == 2

    def test_transient_timeout_retries_then_falls_back(self, with_provider):
        """When every attempt fails on a transient network error, the
        parser must fall back to rule-based parsing — not raise — so the
        CLI stays usable on a flaky network."""
        import httpx

        parser = CommandParser()
        with patch("framelearn.provider_adapter.call_text_llm") as mock_llm:
            mock_llm.side_effect = httpx.TimeoutException("nope")
            with patch("time.sleep"):
                result = parser.parse("什么是 Python")
            # Rule engine treats unparseable text as ask passthrough
            assert result.startswith("ask ")
            # Called initial + 1 retry = 2 attempts
            assert mock_llm.call_count == 2

    def test_non_transient_error_falls_back_to_rules(self, with_provider):
        """401 / 400 are user errors; do not retry them, but DO fall back to
        rule-based parsing so the CLI remains usable."""
        parser = CommandParser()
        from httpx import HTTPStatusError, Request, Response

        req = Request("POST", "https://api.example.com/v1/chat/completions")
        resp = Response(401, request=req, text="bad key")
        err = HTTPStatusError("bad key", request=req, response=resp)

        with patch("framelearn.provider_adapter.call_text_llm") as mock_llm:
            mock_llm.side_effect = err
            result = parser.parse("什么是 Python")
            assert result == "ask 什么是 Python"
            assert mock_llm.call_count == 1  # no retry on 401

    def test_debug_flag_prints_prompt_and_response(self, with_provider, capsys):
        parser = CommandParser(debug=True)
        with patch("framelearn.provider_adapter.call_text_llm") as mock_llm:
            mock_llm.return_value = "ask 什么是 Python"
            parser.parse("什么是 Python")
        out = capsys.readouterr().out
        assert "LLM prompt" in out
        assert "LLM response" in out
        assert "什么是 Python" in out

    def test_debug_flag_off_does_not_print(self, with_provider, capsys):
        parser = CommandParser(debug=False)
        with patch("framelearn.provider_adapter.call_text_llm") as mock_llm:
            mock_llm.return_value = "ask 什么是 Python"
            parser.parse("什么是 Python")
        out = capsys.readouterr().out
        assert "LLM prompt" not in out
        assert "LLM response" not in out


# ------------------------------------------------------------------
# Constructor + flags
# ------------------------------------------------------------------


class TestConstructor:
    def test_default_constructor(self):
        p = CommandParser()
        assert p.debug is False
        assert p.timeout == 5
        assert p.max_retries == 1

    def test_explicit_constructor(self):
        p = CommandParser(debug=True, timeout=2, max_retries=3)
        assert p.debug is True
        assert p.timeout == 2
        assert p.max_retries == 3
