"""Startup cap on refresh tokens stored while the refresh TTL was 0.

It lives in the lifespan, which the conftest ``app`` fixture bypasses, so
these tests drive the real lifespan with Starlette ``TestClient`` as a context
manager.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from starlette.testclient import TestClient

from central_server.app import create_app
from central_server.config import CentralServerConfig
from central_server.db.database import _NEVER_EXPIRES_AT, Database, _hash_token


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


class TestLegacyRefreshTokenCapAtStartup:
    async def test_never_expiring_rows_get_the_refresh_ttl(self, tmp_path):
        config = _config(tmp_path, mobile_refresh_expire_minutes=60)
        await _seed_never_expiring_refresh_token(config.db_path, "r-legacy")

        _start_and_stop(config)

        row = await _refresh_row(config.db_path, "r-legacy")
        assert datetime.fromisoformat(row["expires_at"]) == (
            datetime.fromisoformat(row["created_at"]) + timedelta(minutes=60)
        )

    async def test_zero_refresh_ttl_leaves_them_never_expiring(self, tmp_path):
        config = _config(tmp_path, mobile_refresh_expire_minutes=0)
        await _seed_never_expiring_refresh_token(config.db_path, "r-legacy")

        _start_and_stop(config)

        row = await _refresh_row(config.db_path, "r-legacy")
        assert row["expires_at"] == _NEVER_EXPIRES_AT
