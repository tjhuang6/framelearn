"""Configuration loader for FrameLearn.

Loads settings from settings.toml (normal config) and .env (secrets).
"""

import os
import tomllib
from pathlib import Path
from typing import Any, Optional

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
        get("text.text_mode") → "api"
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
        "text": {
            "text_mode": "api",
            "provider": "deepseek",
            "model": "deepseek-chat",
        },
        "vision": {
            "vision_mode": "api",
            "vision_provider": "siliconflow",
            "vision_model": "Qwen/Qwen3-VL-8B-Instruct",
            "vision_agent_max_retries": 5,
        },
        "privacy": {
            "privacy_hints": False,
        },
        "asr": {
            "mode": "api",
            "provider": "siliconflow",
            "model": "FunAudioLLM/SenseVoiceSmall",
            "language_hints": ["zh", "en"],
            "chunk_duration": 1800,
            "max_workers": 2,
            "poll_interval": 5,
            "poll_timeout": 3600,
            "keep_temp_files": False,
        },
        "video": {
            "output_dir": "./output",
            "scene_threshold": 0.4,
            "similarity_threshold": 0.95,
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
            "detail_level": "detailed",
        },
        "agent": {
            "keyframe_selection": True,
            "quality_review": False,
            "upgrade_model": "",
        },
        "chunking": {
            "segment_minutes": 10,
            "max_images_per_chunk": 20,
            "concurrency": 5,
        },
        "text_clean": {
            "filler_words": [
                "那么",
                "就是说",
                "大家注意",
                "咱们",
                "啊",
                "嗯",
                "这个",
                "那个",
                "对吧",
            ],
        },
        "doc_gen": {
            "srt_filename": "srt_picture.md",
            "blog_filename": "blog.md",
        },
        "heuristic": {
            "scene_threshold": 0.4,
            "similarity_threshold": 0.95,
            "max_frames": 200,
        },
        "blog_gen": {
            "frame_match_tolerance": 2.0,
            "max_retakes": 1,
        },
    }
