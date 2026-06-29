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
from central_server.auth.email import (
    CapturedEmail,
    EmailSender,
    TestEmailSink,
    build_email_sender,
)
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

    def test_empty_explicit_path_is_preserved_not_defaulted(self, monkeypatch):
        """An explicit empty DB_PATH is returned verbatim, not defaulted.

        Production must stay byte-identical to the prior
        ``os.getenv("DB_PATH", default)``: an empty string is an explicit
        value (matched via ``is not None``) and wins over the default. A
        refactor to ``os.getenv(...) or default`` would silently regress this.
        """
        monkeypatch.setenv("JWT_SECRET_KEY", "x")
        monkeypatch.delenv("MOBAIDE_ENV", raising=False)
        monkeypatch.setenv("DB_PATH", "")
        monkeypatch.delenv("MANIFEST_STORE_PATH", raising=False)

        config = CentralServerConfig.from_env()

        assert config.is_test_mode is False
        assert config.db_path == ""
        assert config.manifest_store_path == "data/agent_manifest.json"

    def test_explicit_manifest_path_wins_in_test_mode(self, monkeypatch, tmp_path):
        """MANIFEST_STORE_PATH is authoritative in test mode, like DB_PATH.

        Mirror of ``test_explicit_paths_win_in_test_mode`` for the manifest
        path. DB_PATH is left unset, so the db still falls to the lazily
        created temp dir (cleaned up below).
        """
        monkeypatch.setenv("JWT_SECRET_KEY", "x")
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        explicit_manifest = str(tmp_path / "explicit_manifest.json")
        monkeypatch.setenv("MANIFEST_STORE_PATH", explicit_manifest)
        monkeypatch.delenv("DB_PATH", raising=False)

        config = CentralServerConfig.from_env()
        try:
            assert config.manifest_store_path == explicit_manifest
            assert config.db_path != "central_server.db"
            db_parent = os.path.dirname(config.db_path)
            assert os.path.basename(db_parent).startswith("mobaide-test")
        finally:
            shutil.rmtree(os.path.dirname(config.db_path), ignore_errors=True)


class TestBuildEmailSender:
    """The single selector seam picks a sender by environment."""

    def test_production_returns_real_smtp_sender(self):
        config = CentralServerConfig(jwt_secret_key="x", env="production")
        assert isinstance(build_email_sender(config), EmailSender)

    def test_test_mode_returns_in_process_sink(self):
        config = CentralServerConfig(jwt_secret_key="x", env="test")
        assert isinstance(build_email_sender(config), TestEmailSink)


class TestEmailSinkContract:
    """The sink captures every method AuthService calls, without I/O."""

    async def test_captures_all_five_send_methods(self):
        sink = TestEmailSink()

        await sink.send_verification_email("a@x.io", "123456", 15)
        await sink.send_password_reset_email("b@x.io", "654321", 30, "1.2.3.4")
        await sink.send_password_changed_notification("c@x.io", 2)
        await sink.send_account_deletion_requested(
            "d@x.io", "2026-01-01T00:00:00Z", 14, "https://x/cancel"
        )
        await sink.send_account_deletion_completed("e@x.io")

        assert all(isinstance(m, CapturedEmail) for m in sink.messages)
        assert [m.kind for m in sink.messages] == [
            "verification",
            "password_reset",
            "password_changed",
            "account_deletion_requested",
            "account_deletion_completed",
        ]
        assert sink.messages[0].to == "a@x.io"
        assert sink.messages[0].params["code"] == "123456"
        assert sink.messages[1].params["client_ip"] == "1.2.3.4"
        assert sink.messages[2].params["revoked_session_count"] == 2
        assert sink.messages[3].params["grace_days"] == 14
        assert sink.messages[4].params == {}


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
