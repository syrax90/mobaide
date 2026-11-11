"""MOBAIDE_ENV=test selector: in-process email sink + tmp-path isolation.

Covers the acceptance criteria: production stays the default in every
field, ``MOBAIDE_ENV=test`` swaps the SMTP sender for an in-process capturing
sink (no SMTP ever attempted) and defaults DB/manifest storage to a throwaway
temp dir, and temp-dir teardown survives SQLite WAL sidecars on Windows.

The lifespan-integration test uses Starlette ``TestClient`` as a context
manager (mirroring ``test_heartbeat_flush.py``) because the conftest ``app``
fixture bypasses the lifespan, and the env-selector wiring lives there.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import aiosmtplib
from starlette.testclient import TestClient

from central_server.app import create_app
from central_server.auth.email import TestEmailSink
from central_server.config import CentralServerConfig


class TestFromEnvSelector:
    """``from_env`` parses MOBAIDE_ENV and defaults storage safely."""

    def test_production_is_the_default(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "x")
        for var in ("MOBAIDE_ENV", "DB_PATH", "MANIFEST_STORE_PATH"):
            monkeypatch.delenv(var, raising=False)

        config = CentralServerConfig.from_env()

        assert config.env == "production"
        assert config.is_test_mode is False
        assert config.db_path == "central_server.db"
        assert config.manifest_store_path == "data/agent_manifest.json"

    def test_test_mode_defaults_storage_to_a_temp_dir(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "x")
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        for var in ("DB_PATH", "MANIFEST_STORE_PATH"):
            monkeypatch.delenv(var, raising=False)

        config = CentralServerConfig.from_env()
        try:
            assert config.is_test_mode is True
            # Neither path is the CWD-relative production default, so a
            # disposable instance never litters the working directory.
            assert config.db_path != "central_server.db"
            assert config.manifest_store_path != "data/agent_manifest.json"
            db_parent = os.path.dirname(config.db_path)
            assert os.path.isabs(config.db_path)
            assert os.path.isdir(db_parent)
            assert os.path.basename(db_parent).startswith("mobaide-test")
            # Both land in the same per-instance temp dir.
            assert db_parent == os.path.dirname(config.manifest_store_path)
        finally:
            shutil.rmtree(os.path.dirname(config.db_path), ignore_errors=True)

    def test_explicit_paths_win_in_test_mode(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JWT_SECRET_KEY", "x")
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        explicit_db = str(tmp_path / "explicit.db")
        monkeypatch.setenv("DB_PATH", explicit_db)
        monkeypatch.delenv("MANIFEST_STORE_PATH", raising=False)

        config = CentralServerConfig.from_env()
        try:
            # Field/env injection is authoritative even in test mode — the
            # E2E harness supplies its own tmp paths this way.
            assert config.db_path == explicit_db
        finally:
            shutil.rmtree(
                os.path.dirname(config.manifest_store_path), ignore_errors=True
            )

    def test_non_test_env_values_fall_back_to_production(self, monkeypatch):
        """Only the exact string "test" enables test mode; typos fail safe.

        A misconfigured MOBAIDE_ENV (wrong case, a typo, another environment
        name, or empty) must never silently opt a production instance into
        temp-dir storage or the no-SMTP sink.
        """
        monkeypatch.setenv("JWT_SECRET_KEY", "x")
        for var in ("DB_PATH", "MANIFEST_STORE_PATH"):
            monkeypatch.delenv(var, raising=False)

        for value in ("TEST", "Test", "testing", "staging", "prod", ""):
            monkeypatch.setenv("MOBAIDE_ENV", value)

            config = CentralServerConfig.from_env()

            assert config.is_test_mode is False, value
            assert config.db_path == "central_server.db", value
            assert config.manifest_store_path == "data/agent_manifest.json", value


class TestTestModeAppIntegration:
    """End-to-end through the real lifespan: capture mail, isolate storage."""

    def test_register_captures_mail_and_isolates_storage(self, monkeypatch):
        # Hard guarantee no real SMTP is attempted, even if selection regressed.
        def _boom(*args, **kwargs):
            raise AssertionError("aiosmtplib.send must never run in test mode")

        monkeypatch.setattr(aiosmtplib, "send", _boom)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = os.path.join(tmp, "central_server.db")
            manifest_path = os.path.join(tmp, "agent_manifest.json")
            config = CentralServerConfig(
                jwt_secret_key="test-secret-env-mode",
                env="test",
                email_verification_required=True,
                db_path=db_path,
                manifest_store_path=manifest_path,
            )
            application = create_app(config=config)

            # TestClient as a context manager runs the REAL lifespan, so
            # build_email_sender() actually selects the sink here.
            with TestClient(application) as client:
                assert isinstance(application.state.email_sender, TestEmailSink)

                resp = client.post(
                    "/api/auth/register",
                    json={"email": "sink@example.com", "password": "hunter22"},
                )
                assert resp.status_code == 200
                assert resp.json()["status"] == "verification_required"

                sink = application.state.email_sender
                assert len(sink.messages) == 1
                msg = sink.messages[0]
                assert msg.kind == "verification"
                assert msg.to == "sink@example.com"
                code = msg.params["code"]
                assert isinstance(code, str) and code.isdigit() and len(code) == 6

            # Lifespan shutdown closed the DB; storage stayed under the temp
            # dir, and exiting TemporaryDirectory(ignore_cleanup_errors=True)
            # must not raise even with SQLite WAL sidecars locked on Windows.
            assert os.path.exists(db_path)
