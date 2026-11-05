import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TWSA_DATA_MODE"] = "demo"


@pytest.fixture(autouse=True)
def demo_mode(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_mode", "demo")
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "anthropic_key", "")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    yield
