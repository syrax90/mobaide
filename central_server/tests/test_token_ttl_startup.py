"""Startup handling of token TTLs: the never-expires warning and the cap on
refresh tokens stored while the refresh TTL was 0.

Both live in the lifespan, which the conftest ``app`` fixture bypasses, so
these tests drive the real lifespan with Starlette ``TestClient`` as a context
manager. ``create_app`` replaces the root log handlers that ``caplog`` relies
on, so the module logger is swapped for a recorder instead.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

import central_server.app as app_module
from central_server.app import create_app
from central_server.config import CentralServerConfig
from central_server.db.database import _NEVER_EXPIRES_AT, Database, _hash_token


class _RecordingLogger:
    """Stand-in for the module's structlog logger; keeps each rendered line."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def __getattr__(self, level: str):
        def log(event: str, *args, **_kwargs) -> None:
            self.lines.append((level, event % args if args else event))

        return log

    def matching(self, level: str, prefix: str) -> list[str]:
        return [
            line
            for lvl, line in self.lines
            if lvl == level and line.startswith(prefix)
        ]


@pytest.fixture
def log(monkeypatch) -> _RecordingLogger:
    recorder = _RecordingLogger()
    monkeypatch.setattr(app_module, "logger", recorder)
    return recorder


def _config(tmp_path, **ttls: int) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key="test-secret-key-for-token-ttl-startup",
        db_path=str(tmp_path / "startup.db"),
        manifest_store_path=str(tmp_path / "manifest.json"),
        email_verification_required=False,
        **ttls,
    )


def _start_and_stop(config: CentralServerConfig) -> None:
    with TestClient(create_app(config=config)):
        pass


async def _seed_never_expiring_refresh_token(db_path: str, raw_token: str) -> None:
    db = Database(db_path)
    await db.connect()
    try:
        owner = await db.create_user(email="owner@test.local", password_hash="h")
        agent_id, _ = await db.register_agent("legacy", owner)
        await db.store_refresh_token(raw_token, "device-1", agent_id, 0)
    finally:
        await db.close()


async def _refresh_row(db_path: str, raw_token: str) -> dict:
    db = Database(db_path)
    await db.connect()
    try:
        cursor = await db.conn.execute(
            "SELECT created_at, expires_at FROM mobile_refresh_tokens "
            "WHERE token_hash = ?",
            (_hash_token(raw_token),),
        )
        return dict(await cursor.fetchone())
    finally:
        await db.close()


class TestNeverExpiresWarning:
    def test_names_each_ttl_set_to_zero(self, tmp_path, log):
        _start_and_stop(
            _config(
                tmp_path,
                mobile_token_expire_minutes=0,
                dashboard_token_expire_minutes=0,
            )
        )

        [line] = log.matching("warning", "token_ttl.never_expires")
        assert set(re.findall(r"\b([A-Z_]+_EXPIRE_MINUTES)=0", line)) == {
            "MOBILE_TOKEN_EXPIRE_MINUTES",
            "DASHBOARD_TOKEN_EXPIRE_MINUTES",
        }

    def test_silent_when_every_ttl_is_finite(self, tmp_path, log):
        _start_and_stop(_config(tmp_path))

        assert log.matching("info", "central_server.starting")
        assert log.matching("warning", "token_ttl.never_expires") == []


class TestLegacyRefreshTokenCapAtStartup:
    async def test_never_expiring_rows_get_the_refresh_ttl(self, tmp_path, log):
        config = _config(tmp_path, mobile_refresh_expire_minutes=60)
        await _seed_never_expiring_refresh_token(config.db_path, "r-legacy")

        _start_and_stop(config)

        row = await _refresh_row(config.db_path, "r-legacy")
        assert datetime.fromisoformat(row["expires_at"]) == (
            datetime.fromisoformat(row["created_at"]) + timedelta(minutes=60)
        )
        assert log.matching("info", "mobile_refresh_tokens.capped") == [
            "mobile_refresh_tokens.capped rows=1 ttl_minutes=60"
        ]

    async def test_zero_refresh_ttl_leaves_them_never_expiring(
        self, tmp_path, log
    ):
        config = _config(tmp_path, mobile_refresh_expire_minutes=0)
        await _seed_never_expiring_refresh_token(config.db_path, "r-legacy")

        _start_and_stop(config)

        row = await _refresh_row(config.db_path, "r-legacy")
        assert row["expires_at"] == _NEVER_EXPIRES_AT
        assert log.matching("info", "mobile_refresh_tokens.capped") == []
