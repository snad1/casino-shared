"""Telegram-specific per-user file paths and config persistence.

All functions are parameterized by data_dir and db_filename so each bot
can bind its own values via a thin shim.
"""

import os
import json


def user_dir(data_dir: str, user_id: int) -> str:
    path = os.path.join(data_dir, str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def user_db_path(data_dir: str, db_filename: str, user_id: int) -> str:
    return os.path.join(user_dir(data_dir, user_id), db_filename)


def user_config_path(data_dir: str, user_id: int) -> str:
    return os.path.join(user_dir(data_dir, user_id), "config.json")


def user_presets_path(data_dir: str, user_id: int) -> str:
    return os.path.join(user_dir(data_dir, user_id), "presets.json")


def load_user_config(data_dir: str, user_id: int) -> dict:
    path = user_config_path(data_dir, user_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save_user_config(data_dir: str, user_id: int, config: dict):
    path = user_config_path(data_dir, user_id)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)


def load_presets(data_dir: str, user_id: int) -> dict:
    path = user_presets_path(data_dir, user_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save_presets(data_dir: str, user_id: int, presets: dict):
    path = user_presets_path(data_dir, user_id)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(presets, f, indent=2)
