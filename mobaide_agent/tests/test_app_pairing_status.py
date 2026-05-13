"""Tests for the pairing-pending health-endpoint flag.

When the agent boots with ``RELAY_SERVER_URL`` set but no
``RELAY_AGENT_ID``/``RELAY_AGENT_TOKEN``, ``create_app`` must:

* set ``app.state.pairing_pending = True``
* surface ``pairing_pending: true`` on ``GET /api/health``
* NOT instantiate a ``RelayClient`` (the relay-client startup happens
  inside the lifespan; we verify by avoiding the lifespan in TestClient
  and checking the attribute directly).

This module also covers the in-process pairing transition:
``_start_relay_in_process`` is the seam shared between cold-boot and
post-pair startup, so a test that exercises the helper directly pins
the contract that ``app.state.pairing_pending`` flips to False
atomically with the ``relay_client`` assignment.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import mobaide.agent.app as _agent_app
from mobaide.agent.app import _start_relay_in_process, create_app
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


class TestStartRelayInProcess:
    """Pin the contract that ``_start_relay_in_process`` makes the running
    agent operational in-place after a QR pair, without any process exit.

    Before this design, ``_on_paired`` only scheduled ``sys.exit(75)``
    and the relay client started only on the next process boot. That
    relied on every link in asyncio → uvicorn → mobaide.run →
    mobaide.launcher subprocess wait → supervisor restart to correctly
    propagate exit code 75 — a chain that broke in practice on Windows
    despite the ACL + SvcDoRun fixes in 7b9c581. The in-process
    transition eliminates the dependency: the running process becomes
    the operational process, and ``.env`` is still written as a
    persistence fallback for the next cold boot. Tests in this class
    pin the new behaviour.
    """

    @pytest.fixture(autouse=True)
    def _no_jwt_pre_check(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)

    async def test_starts_relay_client_and_clears_pairing_pending(
        self, tmp_path, monkeypatch,
    ):
        # Build an agent in pairing-pending state, then invoke
        # ``_start_relay_in_process`` with the post-pair RelayConfig
        # and verify ``app.state`` reflects the operational state.
        captured_starts: list[RelayConfig] = []

        class _FakeRelayClient:
            def __init__(self, *, config: RelayConfig, **_kwargs):
                self._config = config
                self._started = False
                self._manifest_cb = None

            async def start(self) -> None:
                captured_starts.append(self._config)
                self._started = True

            async def stop(self) -> None:
                self._started = False

            def set_manifest_update_callback(self, cb):
                self._manifest_cb = cb

        # Patch RelayClient inside the helper's late import so the test
        # never actually opens a network socket. Use the
        # ``mobaide.relay.client`` attribute path because the helper
        # imports it lazily inside the function body.
        from mobaide.relay import client as _relay_client_mod
        monkeypatch.setattr(_relay_client_mod, "RelayClient", _FakeRelayClient)

        # Also patch ``_build_updater`` to return None so we don't pull in
        # the trust-key / install-layout machinery for this unit test.
        monkeypatch.setattr(
            _agent_app, "_build_updater", lambda _app: None,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))
        # ``app.state.session_manager`` etc. are already constructed by
        # ``create_app`` (outside the lifespan); the in-process helper
        # only needs those references plus the new RelayConfig.

        new_relay_cfg = RelayConfig(
            server_url="wss://relay.example.com",
            agent_id="paired-id",
            agent_token="paired-token",
        )

        # Sanity: starting state.
        assert app.state.pairing_pending is True
        assert getattr(app.state, "relay_client", None) is None

        await _start_relay_in_process(app, new_relay_cfg)

        # After the helper, the agent is operational in-process.
        assert app.state.pairing_pending is False, (
            "pairing_pending must flip to False so /api/health and the "
            "mobile app see the agent as paired immediately — without "
            "this, the in-process transition's whole point is defeated"
        )
        assert app.state.relay_client is not None
        # The relay client was constructed with the new config and
        # ``start()`` was awaited.
        assert len(captured_starts) == 1
        assert captured_starts[0].agent_id == "paired-id"
        assert captured_starts[0].agent_token == "paired-token"

    async def test_health_endpoint_reflects_in_process_transition(
        self, tmp_path, monkeypatch,
    ):
        # /api/health is the operator's primary visibility surface;
        # after in-process startup it MUST report
        # ``pairing_pending: false``. If it still reports True, the
        # mobile app keeps polling for pairing completion and the
        # supposed "no restart needed" UX silently regresses to the
        # old restart-based flow.
        class _FakeRelayClient:
            def __init__(self, **_kwargs):
                pass

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            def set_manifest_update_callback(self, _cb):
                pass

        from mobaide.relay import client as _relay_client_mod
        monkeypatch.setattr(_relay_client_mod, "RelayClient", _FakeRelayClient)
        monkeypatch.setattr(
            _agent_app, "_build_updater", lambda _app: None,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))
        client = TestClient(app)

        resp = client.get("/api/health")
        assert resp.json()["pairing_pending"] is True

        await _start_relay_in_process(
            app,
            RelayConfig(
                server_url="wss://relay.example.com",
                agent_id="id1",
                agent_token="tok1",
            ),
        )

        resp = client.get("/api/health")
        assert resp.json()["pairing_pending"] is False, (
            "after in-process pairing transition, /api/health must "
            "report pairing_pending=false — this is the contract the "
            "mobile app polls"
        )

    async def test_failure_leaves_state_consistent_for_fallback(
        self, tmp_path, monkeypatch,
    ):
        # The helper assigns ``app.state.*`` AFTER ``start()`` completes.
        # If ``start()`` raises (e.g. central_server unreachable), the
        # agent must NOT end up with ``pairing_pending=False`` and a
        # broken relay client — that would leave the running process
        # in a half-paired state that ``_on_paired``'s fallback
        # (``sys.exit(75)``) cannot recover from cleanly because the
        # ``.env`` is correct but app.state lies about it.
        class _ExplodingRelayClient:
            def __init__(self, **_kwargs):
                pass

            async def start(self) -> None:
                raise RuntimeError("simulated relay connect failure")

            async def stop(self) -> None:
                pass

            def set_manifest_update_callback(self, _cb):
                pass

        from mobaide.relay import client as _relay_client_mod
        monkeypatch.setattr(_relay_client_mod, "RelayClient", _ExplodingRelayClient)
        monkeypatch.setattr(
            _agent_app, "_build_updater", lambda _app: None,
        )

        app = create_app(config=_make_pairing_pending_config(tmp_path))
        assert app.state.pairing_pending is True

        with pytest.raises(RuntimeError, match="simulated relay connect failure"):
            await _start_relay_in_process(
                app,
                RelayConfig(
                    server_url="wss://relay.example.com",
                    agent_id="id",
                    agent_token="tok",
                ),
            )

        # The helper raised BEFORE the state assignments, so the agent
        # is still in pairing-pending state — consistent for the
        # ``_on_paired`` fallback to ``sys.exit(75)`` to take over.
        assert app.state.pairing_pending is True, (
            "start() failure must NOT flip pairing_pending — assignment "
            "order pins this"
        )
        assert getattr(app.state, "relay_client", None) is None
