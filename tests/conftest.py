"""Tests must never inherit a real model credential from the launch terminal."""
import pytest


@pytest.fixture(autouse=True)
def isolate_model_environment(monkeypatch):
    for key in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "FTTH_API_KEY", "FTTH_PROVIDER",
                "FTTH_MODEL", "FTTH_BASE_URL", "FTTH_MODEL_API", "FTTH_ENABLE_THINKING"):
        monkeypatch.delenv(key, raising=False)
