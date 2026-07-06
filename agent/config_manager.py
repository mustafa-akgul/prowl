"""Configuration manager — manages settings via a JSON file.

All settings changed through the web panel (API keys, prompt templates,
modules) are saved to and read from config.json through this module.
The .env file is used only for initial seeding on first startup.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Config and data directories
CONFIG_DIR = Path(__file__).parent.parent
DATA_DIR = CONFIG_DIR / ".data"
CONFIG_FILE = DATA_DIR / "config.json"
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Single source of truth for review mode → prompt file mapping
MODE_TO_FILE: dict[str, str] = {
    "base": "base_review.md",
    "security": "security_review.md",
    "performance": "performance_review.md",
}


def _get_default_config() -> dict[str, Any]:
    """Returns the default configuration, reading current env vars.

    Using a function instead of a module-level dict ensures that env vars
    updated at runtime (via _apply_config_to_env) are reflected in defaults.
    """
    return {
        "api": {
            "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
            "github_token": os.getenv("GITHUB_TOKEN", ""),
            "github_repo": os.getenv("GITHUB_REPO", ""),
            "jira_url": os.getenv("JIRA_URL", ""),
            "jira_email": os.getenv("JIRA_EMAIL", ""),
            "jira_api_token": os.getenv("JIRA_API_TOKEN", ""),
            "jira_review_status": os.getenv("JIRA_REVIEW_STATUS", "In Review"),
            "jira_ac_field": os.getenv("JIRA_AC_FIELD", "customfield_10001"),
        },
        "review": {
            "provider": "ollama",
            "model": "gemini-2.0-flash",
            "fallback_model": "gemini-1.5-flash",
            "ollama_base_url": "http://localhost:11434",
            "ollama_model": "qwen2.5:7b",
            "max_tokens": 2048,
            "temperature": 0.3,
            "default_mode": "base",
        },
    }


# API key fields that should be masked in frontend responses
SENSITIVE_KEYS = {"gemini_api_key", "github_token", "jira_api_token"}

# In-memory config cache (mtime-based invalidation)
_config_cache: dict[str, Any] | None = None
_config_mtime: float = 0.0


def _ensure_config_exists() -> None:
    """Creates the config file with defaults if it does not exist."""
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Created .data directory.")

    if not CONFIG_FILE.exists():
        # Migrate old config from root directory if present
        old_config = CONFIG_DIR / "config.json"
        if old_config.exists():
            try:
                import shutil

                shutil.move(str(old_config), str(CONFIG_FILE))
                logger.info("Migrated old config to .data/.")
                return
            except Exception as e:
                logger.error("Failed to migrate old config: %s", e)

        save_config(_get_default_config())
        logger.info("Created config file: %s", CONFIG_FILE)


def load_config() -> dict[str, Any]:
    """Loads the config file with mtime-based caching.

    Returns:
        Configuration dictionary.
    """
    global _config_cache, _config_mtime
    _ensure_config_exists()
    try:
        mtime = CONFIG_FILE.stat().st_mtime
        if _config_cache is not None and mtime == _config_mtime:
            return _config_cache
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _config_cache = data
        _config_mtime = mtime
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read config file: %s — using defaults.", exc)
        return _get_default_config().copy()


def save_config(config: dict[str, Any]) -> None:
    """Saves the configuration to disk and invalidates the cache.

    Args:
        config: Configuration dictionary to save.
    """
    global _config_cache, _config_mtime
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        # Update cache immediately
        _config_cache = config
        _config_mtime = CONFIG_FILE.stat().st_mtime
        logger.info("Config saved.")
    except OSError as exc:
        logger.error("Failed to save config: %s", exc)
        raise


def get_api_config() -> dict[str, str]:
    """Returns the API configuration section.

    Returns:
        API settings dictionary.
    """
    config = load_config()
    return config.get("api", _get_default_config()["api"])


def get_review_config() -> dict[str, Any]:
    """Returns the review configuration section.

    Returns:
        Review settings dictionary.
    """
    config = load_config()
    return config.get("review", _get_default_config()["review"])


def mask_sensitive(value: str) -> str:
    """Masks a sensitive value, showing only first and last 4 characters.

    Args:
        value: String to mask.

    Returns:
        Masked string.
    """
    if not value or len(value) <= 12:
        return "•" * len(value) if value else ""
    return f"{value[:4]}{'•' * (len(value) - 8)}{value[-4:]}"


def get_safe_api_config() -> dict[str, str]:
    """Returns API config with sensitive keys masked (for frontend use).

    Returns:
        API settings with masked sensitive values.
    """
    api = get_api_config()
    safe = {}
    for key, value in api.items():
        if key in SENSITIVE_KEYS and value:
            safe[key] = mask_sensitive(value)
        else:
            safe[key] = value
    return safe


def load_prompt(mode: str) -> str:
    """Returns the prompt template for the specified review mode.

    Args:
        mode: Review mode (base, security, performance).

    Returns:
        Prompt template content, or empty string if not found.
    """
    filename = MODE_TO_FILE.get(mode)
    if not filename:
        return ""
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def save_prompt(mode: str, content: str) -> None:
    """Saves a prompt template to disk.

    Args:
        mode: Review mode.
        content: New prompt content.

    Raises:
        ValueError: If the mode is not recognized.
    """
    filename = MODE_TO_FILE.get(mode)
    if not filename:
        raise ValueError(f"Invalid mode: {mode}")
    path = PROMPTS_DIR / filename
    path.write_text(content, encoding="utf-8")
    logger.info("Prompt template updated: %s", path.name)


def get_all_prompts() -> dict[str, str]:
    """Returns all prompt templates.

    Returns:
        Mapping of mode → prompt content.
    """
    return {mode: load_prompt(mode) for mode in MODE_TO_FILE}


# ---------------------------------------------------------------------------
# Async wrappers — offload blocking I/O to thread pool
# ---------------------------------------------------------------------------


async def aload_config() -> dict[str, Any]:
    """Async wrapper for load_config()."""
    return await asyncio.to_thread(load_config)


async def asave_config(config: dict[str, Any]) -> None:
    """Async wrapper for save_config()."""
    await asyncio.to_thread(save_config, config)


async def aload_prompt(mode: str) -> str:
    """Async wrapper for load_prompt()."""
    return await asyncio.to_thread(load_prompt, mode)


async def asave_prompt(mode: str, content: str) -> None:
    """Async wrapper for save_prompt()."""
    await asyncio.to_thread(save_prompt, mode, content)


async def aget_all_prompts() -> dict[str, str]:
    """Returns all prompt templates with parallel file reads."""
    modes = list(MODE_TO_FILE.keys())
    results = await asyncio.gather(*[aload_prompt(m) for m in modes])
    return dict(zip(modes, results))
