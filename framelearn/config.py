"""Configuration loader for FrameLearn.

Loads settings from settings.toml (normal config) and .env (secrets).
"""

import os
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # fallback for older Python

from dotenv import load_dotenv


# Default settings.toml path
_DEFAULT_SETTINGS_PATH = Path(__file__).parent.parent / "settings.toml"

# Cached config
_config_cache: Optional[dict] = None


def load_config(settings_path: Optional[Path] = None) -> dict[str, Any]:
    """Load configuration from settings.toml and .env.

    Args:
        settings_path: Path to settings.toml (default: project root)

    Returns:
        Merged configuration dict
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    # Load .env first (secrets)
    load_dotenv()

    # Load settings.toml
    if settings_path is None:
        settings_path = _DEFAULT_SETTINGS_PATH

    if not settings_path.exists():
        # Use built-in defaults if settings.toml doesn't exist
        config = _default_config()
    else:
        with open(settings_path, "rb") as f:
            config = tomllib.load(f)

    _config_cache = config
    return config


def get(key: str, default: Any = None) -> Any:
    """Get a config value by dot-separated key.

    Examples:
        get("runtime.text_mode") → "appserver"
        get("video.scene_threshold") → 0.3
    """
    config = load_config()
    parts = key.split(".")
    value = config
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return default
        if value is None:
            return default
    return value


def reload():
    """Clear cache and force reload config."""
    global _config_cache
    _config_cache = None


def _default_config() -> dict[str, Any]:
    """Built-in default config when settings.toml is missing."""
    return {
        "runtime": {
            "text_mode": "appserver",
            "vision_mode": "api",
            "asr_mode": "api",
        },
        "appserver": {
            "command": ["codex", "app-server"],
            "workspace": ".",
            "approval_policy": "interactive",
        },
        "video": {
            "output_dir": "./output",
            "scene_threshold": 0.3,
            "fallback_interval": 30,
            "max_keyframes": 100,
            "image_quality": 85,
            "keep_temp_files": False,
        },
        "subtitle": {
            "remove_brackets": True,
            "merge_duplicates": True,
            "timestamp_tolerance": 0.5,
        },
        "style": {
            "tone": "balanced",
            "detail_level": "standard",
        },
    }
