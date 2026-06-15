import pytest
from app.core.config import Settings, get_settings


def test_settings_loads_required_fields(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("JWT_SECRET", "secret")
    get_settings.cache_clear()
    s = get_settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.redis_db == 1
    assert s.jwt_algorithm == "HS256"


def test_settings_missing_jwt_secret_raises(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(Exception):
        Settings(_env_file=None)
