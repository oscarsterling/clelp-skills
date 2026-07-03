"""Configuration loader for yburn.

Loads settings from yburn.yaml in the current directory or ~/.yburn/config.yaml,
with environment variable overrides.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

CONFIG_FILENAMES = [
    Path("yburn.yaml"),
    Path.home() / ".yburn" / "config.yaml",
]


@dataclass
class Config:
    """Yburn configuration."""

    telegram_token: str = ""
    telegram_chat_id: str = ""
    classification_threshold: int = 3
    templates_dir: str = str(Path.home() / ".yburn" / "templates")

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from file and environment variables.

        Searches for yburn.yaml in the current directory first, then
        ~/.yburn/config.yaml. Environment variables override file values.
        """
        file_config = _load_config_file()
        config = cls(
            telegram_token=file_config.get("telegram_token", ""),
            telegram_chat_id=file_config.get("telegram_chat_id", ""),
            classification_threshold=file_config.get("classification_threshold", 3),
            templates_dir=file_config.get(
                "templates_dir", str(Path.home() / ".yburn" / "templates")
            ),
        )
        _apply_env_overrides(config)
        return config


def _load_config_file() -> dict:
    """Search for and load the first config file found."""
    for path in CONFIG_FILENAMES:
        if path.is_file():
            logger.info("Loading config from %s", path)
            try:
                with open(path) as f:
                    data = yaml.safe_load(f)
                return data if isinstance(data, dict) else {}
            except (yaml.YAMLError, OSError) as e:
                logger.warning("Failed to load config from %s: %s", path, e)
                return {}
    logger.debug("No config file found, using defaults")
    return {}


def _apply_env_overrides(config: Config) -> None:
    """Override config values with environment variables."""
    env_map = {
        "YBURN_TELEGRAM_TOKEN": "telegram_token",
        "YBURN_TELEGRAM_CHAT_ID": "telegram_chat_id",
    }
    for env_var, attr in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            logger.debug("Overriding %s from environment", attr)
            setattr(config, attr, value)
