import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Every test gets its own data folder, so nothing touches data/store."""

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DEBUG", "0")
    from config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
