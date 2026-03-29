"""Shared test fixtures."""

import os
import tempfile

import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary SQLite database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary data directory."""
    d = tmp_path / "data"
    d.mkdir()
    return str(d)


@pytest.fixture(autouse=True)
def set_testing_env():
    """Ensure APP_ENV=testing for all tests."""
    old = os.environ.get("APP_ENV")
    os.environ["APP_ENV"] = "testing"
    yield
    if old is None:
        os.environ.pop("APP_ENV", None)
    else:
        os.environ["APP_ENV"] = old
