"""Tests for the pairing-pending health-endpoint flag.

When the agent boots with ``RELAY_SERVER_URL`` set but no
``RELAY_AGENT_ID``/``RELAY_AGENT_TOKEN``, ``create_app`` must:

* set ``app.state.pairing_pending = True``
* surface ``pairing_pending: true`` on ``GET /api/health``
* NOT instantiate a ``RelayClient`` (the relay-client startup happens
  inside the lifespan; we verify by avoiding the lifespan in TestClient
  and checking the attribute directly).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.relay.config import RelayConfig


_TEST_JWT_SECRET = "test-secret-key-for-jwt-signing-do-not-use-in-prod"


def _make_pairing_pending_config(tmp_path) -> AgentConfig:
    relay = RelayConfig(
        server_url="wss://relay.example.com",
        agent_id="",
        agent_token="",
    )
    assert relay.pairing_pending is True
    return AgentConfig(
        jwt_secret_key=_TEST_JWT_SECRET,
        user_store_path=str(tmp_path / "users.json"),
        projects_store_path=str(tmp_path / "projects.json"),
        max_sessions=2,
        log_level="DEBUG",
        relay_config=relay,
    )


def _make_normal_config(tmp_path) -> AgentConfig:
    return AgentConfig(
        jwt_secret_key=_TEST_JWT_SECRET,
        user_store_path=str(tmp_path / "users.json"),
        projects_store_path=str(tmp_path / "projects.json"),
        max_sessions=2,
        log_level="DEBUG",
    )


class TestPairingPendingHealthFlag:
    def test_health_reports_pairing_pending_true(self, tmp_path):
        app = create_app(config=_make_pairing_pending_config(tmp_path))
        # TestClient does NOT enter the FastAPI lifespan unless used as a
        # context manager — so we avoid spinning up the relay-client code path.
        client = TestClient(app)

        resp = client.get("/api/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pairing_pending"] is True

    def test_app_state_exposes_pairing_pending(self, tmp_path):
        app = create_app(config=_make_pairing_pending_config(tmp_path))

        # ``app.state.pairing_pending`` is set during ``create_app`` itself,
        # not in the lifespan, so reading it without a lifespan is valid.
        assert app.state.pairing_pending is True

    def test_no_relay_client_attribute_set_outside_lifespan(self, tmp_path):
        app = create_app(config=_make_pairing_pending_config(tmp_path))

        # The ``relay_client`` attribute is assigned inside ``lifespan`` —
        # by deliberately not entering the lifespan we lock in that nothing
        # in ``create_app`` itself constructs a RelayClient.
        assert getattr(app.state, "relay_client", None) is None


class TestHealthFlagFalseWhenNotPairingPending:
    def test_health_reports_pairing_pending_false_when_relay_unconfigured(
        self, tmp_path,
    ):
        # No relay_config → not pairing-pending.
        app = create_app(config=_make_normal_config(tmp_path))
        client = TestClient(app)

        resp = client.get("/api/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pairing_pending"] is False

    def test_health_reports_pairing_pending_false_when_relay_fully_configured(
        self, tmp_path,
    ):
        relay = RelayConfig(
            server_url="wss://relay.example.com",
            agent_id="paired-id",
            agent_token="paired-token",
        )
        assert relay.pairing_pending is False

        cfg = AgentConfig(
            jwt_secret_key=_TEST_JWT_SECRET,
            user_store_path=str(tmp_path / "users.json"),
            projects_store_path=str(tmp_path / "projects.json"),
            max_sessions=2,
            log_level="DEBUG",
            relay_config=relay,
        )
        app = create_app(config=cfg)
        client = TestClient(app)

        resp = client.get("/api/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["pairing_pending"] is False


class TestHealthEndpointShape:
    def test_response_has_expected_keys(self, tmp_path):
        app = create_app(config=_make_pairing_pending_config(tmp_path))
        client = TestClient(app)

        resp = client.get("/api/health")
        body = resp.json()

        for key in ("status", "sessions", "max_sessions", "pairing_pending"):
            assert key in body, f"missing key in health payload: {key}"
        assert body["status"] == "ok"


@pytest.fixture
def _no_projects_env(monkeypatch):
    """Ensure ``os.environ`` is decoupled from the seed mechanism.

    The whole point of the ``config.projects`` field + ``seed_json=`` arg
    is that the seed value comes from the settings instance, NOT from
    ``os.getenv("PROJECTS")``. We unset PROJECTS so a passing test
    cannot be a false positive caused by env-var leakage.
    """
    monkeypatch.delenv("PROJECTS", raising=False)


_ = _no_projects_env  # silence pyright "not accessed" hint


class TestProjectsStoreSeededFromConfig:
    """Verify ``create_app`` passes ``config.projects`` to
    ``ProjectsStore.load(seed_json=...)``.

    Pre-CLA-48 the store read ``os.getenv("PROJECTS")`` directly, which
    silently missed `.env`-defined values on Windows (the supervisor
    does not pre-load the env file). Post-CLA-48 the seed flows through
    the pydantic-settings instance.
    """

    def test_create_app_seeds_projects_from_config(
        self, tmp_path, _no_projects_env,
    ):
        seed = json.dumps({"Alpha": {"path": str(tmp_path / "alpha")}})
        cfg = AgentConfig(
            jwt_secret_key=_TEST_JWT_SECRET,
            user_store_path=str(tmp_path / "users.json"),
            projects_store_path=str(tmp_path / "projects.json"),
            max_sessions=2,
            log_level="DEBUG",
            projects=seed,
        )

        app = create_app(config=cfg)

        # ProjectsStore.load(seed_json=...) populated the store and
        # persisted to disk.
        store = app.state.projects_store
        names = {p["name"] for p in store.list_public()}
        assert "Alpha" in names
        on_disk = json.loads(
            (tmp_path / "projects.json").read_text(encoding="utf-8"),
        )
        assert any(entry["name"] == "Alpha" for entry in on_disk)

    def test_create_app_with_no_projects_seed_creates_empty_store(
        self, tmp_path, _no_projects_env,
    ):
        cfg = AgentConfig(
            jwt_secret_key=_TEST_JWT_SECRET,
            user_store_path=str(tmp_path / "users.json"),
            projects_store_path=str(tmp_path / "projects.json"),
            max_sessions=2,
            log_level="DEBUG",
            projects=None,
        )

        app = create_app(config=cfg)

        # Empty seed → empty store on disk; no spurious entries from
        # leaking host environment.
        store = app.state.projects_store
        assert store.list_public() == []

    def test_explicit_seed_takes_precedence_over_environ_projects(
        self, tmp_path, monkeypatch,
    ):
        # When both ``config.projects`` and ``os.environ['PROJECTS']`` are
        # set, the config-supplied value wins because ``app.create_app``
        # passes it as the explicit ``seed_json=`` arg, and ``ProjectsStore.
        # load`` only falls back to ``os.getenv`` when the arg is None.
        # This pins the os.environ-decoupling fix.
        monkeypatch.setenv(
            "PROJECTS",
            json.dumps({"FromEnvLeak": {"path": "/should/not/show/up"}}),
        )
        config_seed = json.dumps({"FromConfig": {"path": str(tmp_path)}})
        cfg = AgentConfig(
            jwt_secret_key=_TEST_JWT_SECRET,
            user_store_path=str(tmp_path / "users.json"),
            projects_store_path=str(tmp_path / "projects.json"),
            max_sessions=2,
            log_level="DEBUG",
            projects=config_seed,
        )

        app = create_app(config=cfg)

        names = {p["name"] for p in app.state.projects_store.list_public()}
        assert "FromConfig" in names
        # The env-var value is ignored — seed_json takes precedence.
        assert "FromEnvLeak" not in names
