"""Regression test for JsonRpcStdioClient environment variable filtering.

Verifies that sensitive credentials (API keys, secrets, cloud credentials)
are NOT passed to the Codex subprocess, while system variables are preserved.
"""

import os
from unittest.mock import patch

import pytest

from framelearn.app_server.jsonrpc_client import JsonRpcStdioClient


class TestEnvironmentFiltering:
    """Test suite for _build_env() credential filtering."""

    def test_blocks_framelearn_api_keys(self):
        """Block FrameLearn's own API keys from subprocess."""
        mock_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            # FrameLearn credentials that should be blocked
            "SILICONFLOW_API_KEY": "sk-silicon-secret",
            "DASHSCOPE_API_KEY": "dash-secret",
            "OSS_ACCESS_KEY_ID": "oss-id-secret",
            "OSS_ACCESS_KEY_SECRET": "oss-secret-secret",
            "TEXT_API_KEY": "text-secret",
            "VISION_API_KEY": "vision-secret",
        }

        with patch.dict(os.environ, mock_env, clear=True):
            env = JsonRpcStdioClient._build_env(override=None)

            # System variables should pass
            assert env["PATH"] == "/usr/bin"
            assert env["HOME"] == "/home/user"

            # All API keys should be blocked
            assert "SILICONFLOW_API_KEY" not in env
            assert "DASHSCOPE_API_KEY" not in env
            assert "OSS_ACCESS_KEY_ID" not in env
            assert "OSS_ACCESS_KEY_SECRET" not in env
            assert "TEXT_API_KEY" not in env
            assert "VISION_API_KEY" not in env

    def test_blocks_generic_secrets(self):
        """Block generic secrets and credentials."""
        mock_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            # Generic secrets that should be blocked
            "DATABASE_URL": "postgres://secret",
            "WEBHOOK_SECRET": "webhook-secret",
            "AWS_ACCESS_KEY_ID": "aws-key",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "AZURE_CLIENT_SECRET": "azure-secret",
            "GCP_SERVICE_ACCOUNT_KEY": "gcp-secret",
            "OPENAI_API_KEY": "openai-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
        }

        with patch.dict(os.environ, mock_env, clear=True):
            env = JsonRpcStdioClient._build_env(override=None)

            # All secrets should be blocked (not in allowlist)
            assert "DATABASE_URL" not in env
            assert "WEBHOOK_SECRET" not in env
            assert "AWS_ACCESS_KEY_ID" not in env
            assert "AWS_SECRET_ACCESS_KEY" not in env
            assert "AZURE_CLIENT_SECRET" not in env
            assert "GCP_SERVICE_ACCOUNT_KEY" not in env
            assert "OPENAI_API_KEY" not in env
            assert "ANTHROPIC_API_KEY" not in env

    def test_preserves_system_variables(self):
        """Essential system variables should pass through."""
        mock_env = {
            # Core system
            "PATH": "/usr/local/bin:/usr/bin",
            "HOME": "/home/testuser",
            "USER": "testuser",
            "SHELL": "/bin/bash",
            "TMPDIR": "/tmp",
            # Locale
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TERM": "xterm-256color",
            # Development tools
            "SSH_AUTH_SOCK": "/tmp/ssh-agent.sock",
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            # XDG
            "XDG_CONFIG_HOME": "/home/testuser/.config",
        }

        with patch.dict(os.environ, mock_env, clear=True):
            env = JsonRpcStdioClient._build_env(override=None)

            # All system variables should pass
            assert env["PATH"] == "/usr/local/bin:/usr/bin"
            assert env["HOME"] == "/home/testuser"
            assert env["USER"] == "testuser"
            assert env["SHELL"] == "/bin/bash"
            assert env["TMPDIR"] == "/tmp"
            assert env["LANG"] == "en_US.UTF-8"
            assert env["LC_ALL"] == "en_US.UTF-8"
            assert env["TERM"] == "xterm-256color"
            assert env["SSH_AUTH_SOCK"] == "/tmp/ssh-agent.sock"
            assert env["GIT_AUTHOR_NAME"] == "Test User"
            assert env["GIT_AUTHOR_EMAIL"] == "test@example.com"
            assert env["XDG_CONFIG_HOME"] == "/home/testuser/.config"

    def test_preserves_codex_variables(self):
        """CODEX_* variables should always pass through."""
        mock_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "CODEX_HOME": "/custom/codex",
            "CODEX_DEBUG": "1",
            "CODEX_LOG_LEVEL": "debug",
            "CODEX_CUSTOM_FLAG": "value",
            # Non-CODEX should be blocked if not in allowlist
            "CUSTOM_API_KEY": "secret",
        }

        with patch.dict(os.environ, mock_env, clear=True):
            env = JsonRpcStdioClient._build_env(override=None)

            # All CODEX_* should pass
            assert env["CODEX_HOME"] == "/custom/codex"
            assert env["CODEX_DEBUG"] == "1"
            assert env["CODEX_LOG_LEVEL"] == "debug"
            assert env["CODEX_CUSTOM_FLAG"] == "value"

            # Non-CODEX secret should be blocked
            assert "CUSTOM_API_KEY" not in env

    def test_override_parameter(self):
        """Override parameter can explicitly pass additional variables."""
        mock_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "BLOCKED_VAR": "should-not-appear",
        }

        override = {
            "CUSTOM_VAR": "custom-value",
            "ANOTHER_VAR": "another-value",
        }

        with patch.dict(os.environ, mock_env, clear=True):
            env = JsonRpcStdioClient._build_env(override=override)

            # System vars pass
            assert env["PATH"] == "/usr/bin"
            assert env["HOME"] == "/home/user"

            # Override vars added
            assert env["CUSTOM_VAR"] == "custom-value"
            assert env["ANOTHER_VAR"] == "another-value"

            # Non-allowlisted env var still blocked
            assert "BLOCKED_VAR" not in env

    def test_override_can_overwrite_system_vars(self):
        """Override parameter can overwrite allowlisted variables."""
        mock_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
        }

        override = {
            "PATH": "/custom/path",  # Overwrite
            "HOME": "/custom/home",  # Overwrite
        }

        with patch.dict(os.environ, mock_env, clear=True):
            env = JsonRpcStdioClient._build_env(override=override)

            assert env["PATH"] == "/custom/path"
            assert env["HOME"] == "/custom/home"

    def test_empty_environment(self):
        """Should work even with minimal environment."""
        mock_env = {}

        with patch.dict(os.environ, mock_env, clear=True):
            env = JsonRpcStdioClient._build_env(override=None)

            # Should return empty dict or minimal set
            assert isinstance(env, dict)
            # No secrets should leak
            assert all(
                not key.endswith("_KEY")
                and not key.endswith("_SECRET")
                and "PASSWORD" not in key
                for key in env.keys()
            )

    def test_allowlist_is_sufficient_for_codex(self):
        """Verify allowlist includes all variables Codex typically needs."""
        # Simulate a realistic development environment
        mock_env = {
            # Essential for Codex subprocess
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/home/dev",
            "USER": "dev",
            "SHELL": "/bin/zsh",
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
            # Git identity (needed for commits)
            "GIT_AUTHOR_NAME": "Developer",
            "GIT_AUTHOR_EMAIL": "dev@example.com",
            # SSH agent (for git operations)
            "SSH_AUTH_SOCK": "/tmp/ssh-agent",
            # Node.js (for npm/npx)
            "NODE_ENV": "development",
            # Codex config
            "CODEX_HOME": "/home/dev/.codex",
            # Should be blocked
            "SILICONFLOW_API_KEY": "sk-blocked",
            "DATABASE_URL": "postgres://blocked",
        }

        with patch.dict(os.environ, mock_env, clear=True):
            env = JsonRpcStdioClient._build_env(override=None)

            # Essential variables for Codex operations
            required_for_codex = [
                "PATH",
                "HOME",
                "USER",
                "SHELL",
                "LANG",
                "TERM",
                "GIT_AUTHOR_NAME",
                "GIT_AUTHOR_EMAIL",
                "SSH_AUTH_SOCK",
                "NODE_ENV",
                "CODEX_HOME",
            ]

            for key in required_for_codex:
                assert key in env, f"Required variable {key} missing from subprocess env"

            # Secrets should be blocked
            assert "SILICONFLOW_API_KEY" not in env
            assert "DATABASE_URL" not in env

    def test_no_env_key_patterns_leak(self):
        """Comprehensive check: no common secret patterns should leak."""
        mock_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            # Various secret patterns
            "API_KEY": "secret1",
            "SOME_API_KEY": "secret2",
            "SECRET": "secret3",
            "SOME_SECRET": "secret4",
            "PASSWORD": "secret5",
            "DB_PASSWORD": "secret6",
            "TOKEN": "secret7",
            "ACCESS_TOKEN": "secret8",
            "PRIVATE_KEY": "secret9",
            "CLIENT_SECRET": "secret10",
        }

        with patch.dict(os.environ, mock_env, clear=True):
            env = JsonRpcStdioClient._build_env(override=None)

            # No secrets should leak
            for key in mock_env:
                if any(
                    pattern in key
                    for pattern in ["KEY", "SECRET", "PASSWORD", "TOKEN", "PRIVATE"]
                ):
                    if key != "PATH" and key != "HOME":  # These are safe
                        assert key not in env, f"Secret variable {key} leaked to subprocess"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
