"""Tests for shared/tg/database.py — per-user file paths and persistence."""

import json
import os
import stat

import pytest

from shared.tg.database import (
    user_dir, user_db_path, user_config_path, user_presets_path,
    load_user_config, save_user_config,
    load_presets, save_presets,
)


class TestUserPaths:
    def test_user_dir_creates_directory(self, tmp_data_dir):
        path = user_dir(tmp_data_dir, 12345)
        assert os.path.isdir(path)
        assert path.endswith("12345")

    def test_user_db_path(self, tmp_data_dir):
        path = user_db_path(tmp_data_dir, "test.db", 12345)
        assert path.endswith("test.db")
        assert "12345" in path

    def test_user_config_path(self, tmp_data_dir):
        path = user_config_path(tmp_data_dir, 12345)
        assert path.endswith("config.json")

    def test_user_presets_path(self, tmp_data_dir):
        path = user_presets_path(tmp_data_dir, 12345)
        assert path.endswith("presets.json")


class TestUserConfig:
    def test_save_and_load(self, tmp_data_dir):
        config = {"strategy": "2", "base_bet": 0.001, "currency": "BTC"}
        save_user_config(tmp_data_dir, 12345, config)
        loaded = load_user_config(tmp_data_dir, 12345)
        assert loaded == config

    def test_load_missing_returns_empty(self, tmp_data_dir):
        assert load_user_config(tmp_data_dir, 99999) == {}

    def test_save_creates_with_0600_perms(self, tmp_data_dir):
        save_user_config(tmp_data_dir, 12345, {"test": True})
        path = user_config_path(tmp_data_dir, 12345)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_load_corrupt_returns_empty(self, tmp_data_dir):
        user_dir(tmp_data_dir, 12345)
        path = user_config_path(tmp_data_dir, 12345)
        with open(path, "w") as f:
            f.write("not valid json{{{")
        assert load_user_config(tmp_data_dir, 12345) == {}


class TestPresets:
    def test_save_and_load(self, tmp_data_dir):
        presets = {"safe": {"base_bet": 0.0001}, "aggressive": {"base_bet": 0.01}}
        save_presets(tmp_data_dir, 12345, presets)
        loaded = load_presets(tmp_data_dir, 12345)
        assert loaded == presets

    def test_load_missing_returns_empty(self, tmp_data_dir):
        assert load_presets(tmp_data_dir, 99999) == {}

    def test_save_creates_with_0600_perms(self, tmp_data_dir):
        save_presets(tmp_data_dir, 12345, {"p1": {}})
        path = user_presets_path(tmp_data_dir, 12345)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600
