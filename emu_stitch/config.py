"""
Config module for emu-stitch: persistent user preferences stored in
~/.config/emu-stitch/config.json (e.g. backup retention count).
"""

from __future__ import annotations

import os
import json
import logging
from typing import Any, Dict

DEFAULT_BACKUP_RETENTION = 3


def _config_path() -> str:
    return os.path.join(os.path.expanduser("~/.config/emu-stitch"), "config.json")


def is_configured() -> bool:
    """Whether the config file has been written at least once."""
    return os.path.exists(_config_path())


def load_config() -> Dict[str, Any]:
    config_path = _config_path()
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading config file: {e}")
        return {}


def save_config(config: Dict[str, Any]) -> None:
    config_path = _config_path()
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    tmp_path = f"{config_path}.tmp-{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, config_path)


def get_backup_retention() -> int:
    """Number of `.bak-*` backups to keep per path. 0 means keep all forever."""
    value = load_config().get("backup_retention", DEFAULT_BACKUP_RETENTION)
    if not isinstance(value, int) or value < 0:
        return DEFAULT_BACKUP_RETENTION
    return value


def set_backup_retention(count: int) -> None:
    config = load_config()
    config["backup_retention"] = count
    save_config(config)
