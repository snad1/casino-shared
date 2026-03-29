"""Tests for shared/web/services.py — system services and metrics."""

import pytest

from shared.web.services import (
    system_metrics, format_bytes, make_services,
)


class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(500) == "500.0 B"

    def test_kilobytes(self):
        assert format_bytes(1024) == "1.0 KB"

    def test_megabytes(self):
        assert format_bytes(1024 * 1024) == "1.0 MB"

    def test_gigabytes(self):
        assert format_bytes(1024 ** 3) == "1.0 GB"

    def test_terabytes(self):
        assert format_bytes(1024 ** 4) == "1.0 TB"

    def test_zero(self):
        assert format_bytes(0) == "0.0 B"


class TestSystemMetrics:
    def test_returns_all_fields(self):
        m = system_metrics()
        assert "cpu_percent" in m
        assert "memory_total" in m
        assert "memory_used" in m
        assert "memory_percent" in m
        assert "disk_total" in m
        assert "disk_used" in m
        assert "disk_percent" in m
        assert "load_1m" in m

    def test_values_are_numeric(self):
        m = system_metrics()
        assert isinstance(m["cpu_percent"], (int, float))
        assert isinstance(m["memory_total"], int)


class TestMakeServices:
    def test_returns_expected_keys(self):
        settings = type("S", (), {
            "service_cli": "bot", "service_tg": "bot-tg",
            "service_web": "bot-web", "repo_dir": "/tmp",
            "install_dir": "/tmp",
        })()
        svc = make_services(settings, "bot.py")
        assert "SERVICES" in svc
        assert "all_service_statuses" in svc
        assert "git_pull" in svc
        assert "run_update" in svc
        assert svc["SERVICES"]["cli"] == "bot"
