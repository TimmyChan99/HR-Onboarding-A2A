from __future__ import annotations

from app.config import Settings


def test_a2a_client_timeout_seconds_loads_from_env(monkeypatch) -> None:
    monkeypatch.setenv("A2A_CLIENT_TIMEOUT_SECONDS", "123.5")

    settings = Settings(_env_file=None)

    assert settings.a2a_client_timeout_seconds == 123.5
