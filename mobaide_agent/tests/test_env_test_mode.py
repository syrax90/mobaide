"""Agent test-mode (``MOBAIDE_ENV=test``) selector, fake PTY backend, and
temp-path isolation.

Mirrors the central_server test shape: config-selector branches, the
backend-factory selector, the FakePtyBackend echo + teardown contract, and a
real ``create_app`` integration proving a disposable instance spawns no real
shell, echoes input through the normal output-frame path, and writes only under
a temp dir.

Acceptance criteria covered:
  * production default unchanged; only the exact value ``test`` opts in;
  * ``MOBAIDE_ENV=test`` selects ``FakePtyBackend`` (no real shell / pid None);
  * the fake cannot busy-spin or hang (blocking read + prompt EOF on terminate);
  * test mode anchors state under a temp dir, never the install/state prefix.
"""
from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path

import pytest

from mobaide.agent import install_paths
from mobaide.agent.app import create_app
from mobaide.agent.config import AgentConfig
from mobaide.core import FakePtyBackend, SessionManager, create_pty_backend
from mobaide.core.models import SessionConfig, SessionState
from mobaide.core.session import GRACEFUL_TERM_SECONDS, TerminalSession
from tests.conftest import TEST_JWT_SECRET


@pytest.fixture(autouse=True)
def _clean_test_mode_env(monkeypatch):
    """Keep host values for the selectors under test from leaking in, so the
    production-default assertions are deterministic on any machine/CI."""
    for var in (
        "MOBAIDE_ENV",
        "MOBAIDE_AGENT_PREFIX",
        "MOBAIDE_AGENT_ALLOW_INSECURE_WS",
    ):
        monkeypatch.delenv(var, raising=False)


def _shell_config(session_id: str = "echo-1") -> SessionConfig:
    # The command is irrelevant to the fake — it never spawns a process.
    return SessionConfig(
        session_id=session_id,
        command=["fake-shell"],
        rows=24,
        cols=80,
        scrollback_size=100,
    )


async def _drain_output_containing(
    session: TerminalSession, needle: bytes, timeout: float = 3.0
) -> bytes:
    """Accumulate ``output`` event payloads until ``needle`` appears.

    The synthetic ``started`` event shares the queue; this skips non-output
    frames and returns the accumulated output buffer once it contains ``needle``.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    buf = b""
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AssertionError(
                f"output containing {needle!r} not seen; got {buf!r}"
            )
        try:
            event = await asyncio.wait_for(
                session.event_queue.get(), timeout=remaining
            )
        except asyncio.TimeoutError as exc:
            raise AssertionError(
                f"output containing {needle!r} not seen; got {buf!r}"
            ) from exc
        if event.event_type == "output" and event.data:
            buf += event.data
            if needle in buf:
                return buf


class TestAgentConfigEnvSelector:
    def test_production_is_the_default(self):
        cfg = AgentConfig(jwt_secret_key=TEST_JWT_SECRET)
        assert cfg.env == "production"
        assert cfg.is_test_mode is False

    def test_exact_test_value_opts_in(self):
        cfg = AgentConfig(jwt_secret_key=TEST_JWT_SECRET, env="test")
        assert cfg.is_test_mode is True

    @pytest.mark.parametrize(
        "value", ["TEST", "Test", "testing", "staging", "prod", ""]
    )
    def test_non_test_values_fall_back_to_production(self, value):
        # Exact-string contract: only the literal "test" opts in.
        cfg = AgentConfig(jwt_secret_key=TEST_JWT_SECRET, env=value)
        assert cfg.is_test_mode is False

    def test_reads_mobaide_env_var(self, monkeypatch):
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        cfg = AgentConfig(jwt_secret_key=TEST_JWT_SECRET)
        assert cfg.env == "test"
        assert cfg.is_test_mode is True


class TestStatePrefixIsolation:
    def test_test_mode_anchors_to_disposable_temp_dir(self, monkeypatch):
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        monkeypatch.setattr(install_paths, "_test_state_prefix", None)
        prefix = install_paths.state_prefix()
        try:
            assert prefix.is_absolute()
            assert prefix.exists()
            assert prefix.name.startswith("mobaide-agent-test-")
            # Cached: a second call returns the same disposable dir so every
            # state file lands together.
            assert install_paths.state_prefix() == prefix
        finally:
            shutil.rmtree(prefix, ignore_errors=True)

    def test_explicit_prefix_wins_even_in_test_mode(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(tmp_path))
        monkeypatch.setattr(install_paths, "_test_state_prefix", None)
        assert install_paths.state_prefix() == tmp_path

    def test_production_does_not_use_a_test_temp_dir(self, monkeypatch):
        # production (env cleared by the autouse fixture)
        monkeypatch.setattr(install_paths, "_test_state_prefix", None)
        assert install_paths.is_test_mode() is False
        assert not install_paths.state_prefix().name.startswith(
            "mobaide-agent-test-"
        )

    def test_install_paths_is_test_mode_true_only_for_exact_test(
        self, monkeypatch
    ):
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        assert install_paths.is_test_mode() is True

    @pytest.mark.parametrize(
        "value", ["TEST", "Test", "testing", "staging", "production", ""]
    )
    def test_install_paths_is_test_mode_rejects_non_exact_values(
        self, monkeypatch, value
    ):
        # The file-isolation gate must use the same exact-string contract as
        # AgentConfig.is_test_mode so the two independent readers of MOBAIDE_ENV
        # (documented as a known wrinkle) can never disagree and reroute state.
        monkeypatch.setenv("MOBAIDE_ENV", value)
        assert install_paths.is_test_mode() is False


@pytest.mark.timeout(15)
class TestFakePtyBackendContract:
    def test_echo_roundtrip_and_no_process(self):
        backend = FakePtyBackend()
        backend.spawn(["x"], None, None, 24, 80)
        try:
            assert backend.is_alive() is True
            assert backend.get_pid() is None  # no OS process
            backend.write(b"hello world")
            assert backend.read() == b"hello world"
        finally:
            backend.close()

    def test_read_respects_size_and_stashes_remainder(self):
        backend = FakePtyBackend()
        backend.spawn(["x"], None, None, 24, 80)
        try:
            backend.write(b"abcdef")
            assert backend.read(3) == b"abc"
            assert backend.read(3) == b"def"
        finally:
            backend.close()

    def test_leftover_is_drained_before_eof_after_terminate(self):
        # The read() order is load-bearing: a stashed remainder is served
        # *before* the dead-and-drained EOF short-circuit, so a chunk that was
        # only partially read is never dropped when the session tears down
        # mid-read. A reorder (dead-check first) would lose buffered output.
        backend = FakePtyBackend()
        backend.spawn(["x"], None, None, 24, 80)
        backend.write(b"abcdef")
        assert backend.read(3) == b"abc"  # stashes b"def" in _leftover
        backend.terminate()
        assert backend.read(3) == b"def"  # leftover survives terminate()
        assert backend.read() == b""  # now truly drained -> immediate EOF

    def test_read_blocks_then_wakes_on_terminate(self):
        # The load-bearing invariant: read() blocks while alive+empty (so the
        # session reader loop never busy-spins) and wakes with EOF on
        # terminate() (so teardown never hangs).
        backend = FakePtyBackend()
        backend.spawn(["x"], None, None, 24, 80)
        got: list[bytes] = []

        def _reader() -> None:
            got.append(backend.read())

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        time.sleep(0.2)
        assert got == [], "read() returned while alive+empty — must block"
        assert t.is_alive(), "reader exited early — busy-spin risk"

        backend.terminate()
        t.join(timeout=2.0)
        assert not t.is_alive(), "read() did not wake on terminate() — HANG"
        assert got == [b""]
        assert backend.is_alive() is False

    def test_eof_is_idempotent_and_never_blocks_when_dead(self):
        backend = FakePtyBackend()
        backend.spawn(["x"], None, None, 24, 80)
        backend.terminate()
        assert backend.read() == b""
        assert backend.read() == b""  # dead+drained → immediate EOF, no block
        backend.close()
        assert backend.read() == b""


class TestBackendFactorySelection:
    def test_default_factory_is_the_real_backend(self):
        mgr = SessionManager()
        assert mgr._backend_factory is create_pty_backend

    async def test_fake_factory_is_used_for_created_sessions(self):
        mgr = SessionManager(backend_factory=FakePtyBackend)
        try:
            session = await mgr.create_session(_shell_config("sel-1"))
            assert isinstance(session._backend, FakePtyBackend)
            assert session.pid is None
        finally:
            await mgr.shutdown()


@pytest.mark.timeout(15)
class TestFakePtyTeardown:
    async def test_stop_is_prompt_and_clean(self):
        session = TerminalSession(_shell_config("td-1"), backend=FakePtyBackend())
        await session.start()
        assert session.state == SessionState.RUNNING

        start = time.monotonic()
        await session.stop()  # force=False
        elapsed = time.monotonic() - start

        assert session.state == SessionState.STOPPED
        # terminate() flips is_alive()->False, so the graceful-kill grace wait
        # is skipped entirely — teardown is near-instant, never 2s+.
        assert elapsed < GRACEFUL_TERM_SECONDS, f"stop took {elapsed:.2f}s"

    async def test_double_stop_does_not_raise(self):
        session = TerminalSession(_shell_config("td-2"), backend=FakePtyBackend())
        await session.start()
        await session.stop()
        await session.stop()
        assert session.state == SessionState.STOPPED


@pytest.mark.timeout(20)
class TestTestModeAppIntegration:
    def test_create_app_test_mode_wires_fake_and_isolates_paths(
        self, monkeypatch, tmp_path
    ):
        # Real create_app wiring: env="test" selects the fake backend factory,
        # and explicit temp store paths keep all state under tmp_path.
        monkeypatch.setenv("MOBAIDE_ENV", "test")
        cfg = AgentConfig(
            jwt_secret_key=TEST_JWT_SECRET,
            env="test",
            user_store_path=str(tmp_path / "users.json"),
            projects_store_path=str(tmp_path / "projects.json"),
        )
        assert cfg.is_test_mode is True

        app = create_app(config=cfg)
        # create_app selects the fake backend factory in test mode...
        assert app.state.session_manager._backend_factory is FakePtyBackend
        # ...and all mutable state resolves under tmp_path, never the real
        # install/state prefix.
        assert Path(cfg.user_store_path).parent == tmp_path
        assert Path(cfg.projects_store_path).parent == tmp_path

    def test_create_app_production_mode_wires_the_real_backend(self, tmp_path):
        # Acceptance criterion #1: with MOBAIDE_ENV unset (cleared by the
        # autouse fixture) create_app must wire the *real* platform backend, so
        # a production boot never silently ships the in-process echo fake. Pins
        # the else-branch of the create_app backend-factory selector, which the
        # bare-SessionManager default test above cannot catch.
        cfg = AgentConfig(
            jwt_secret_key=TEST_JWT_SECRET,
            user_store_path=str(tmp_path / "users.json"),
            projects_store_path=str(tmp_path / "projects.json"),
        )
        assert cfg.is_test_mode is False

        app = create_app(config=cfg)
        assert app.state.session_manager._backend_factory is create_pty_backend

    async def test_echo_through_the_output_frame_path(self):
        # A session created through the manager echoes written input back as an
        # "output" SessionEvent — the same reader-loop path the WS fanout
        # consumes downstream — without spawning a real shell. Uses a bare
        # SessionManager (no fanout) so the test owns the event queue.
        mgr = SessionManager(backend_factory=FakePtyBackend)
        try:
            session = await mgr.create_session(_shell_config("echo-int"))
            assert isinstance(session._backend, FakePtyBackend)
            assert session.pid is None  # no real shell process

            await session.write(b"ping-123\n")
            out = await _drain_output_containing(session, b"ping-123")
            assert b"ping-123" in out
        finally:
            await mgr.shutdown()
