"""End-to-end: an unpaired agent's QR pairing loop against a real relay.

Reproduces the production incident where one unpaired agent called
``POST /api/pairing/initiate`` every ~4 s for hours. Each scenario boots a real
``python -m mobaide`` with no credentials, so its boot-time pairing monitor
runs. The agent reaches a real ``python -m central_server`` (with a signing
keypair, so QR pairing works) through ``FaultProxy``, a loopback TCP proxy
that counts initiates and injects WebSocket faults on ``/ws/pairing/``.

Scenarios:

* healthy pairing: one initiate, claim, agent comes online;
* WS upgrade fails with 502 or a dropped connection: the nonce is reused,
  so there is one initiate while it is valid;
* WS upgrade always refused with 403: one extra initiate, then reuse;
* the first ``ready`` frame is lost: the relay keeps the waiter, the retry
  gets 403 ``duplicate``, the agent mints one fresh nonce and still pairs;
* agent clock ahead of the relay: the ``clock_skew`` warning fires and
  initiates back off instead of looping at ~4 s;
* per-IP initiate cap on the relay: 429 with ``Retry-After`` for raw
  callers, the ``pair-qr`` message, and an agent that backs off.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from conftest import (
    E2E_ADMIN_EMAIL,
    E2E_ADMIN_PASSWORD,
    _agent_ready,
    _free_port,
    _make_starter,
    _server_ready,
    _terminate,
)
from mock_mobile_client import MockMobileClient

pytestmark = pytest.mark.timeout(180)

_OWNER_EMAIL = "e2e-pairing-loop@test.local"
_OWNER_PASSWORD = "E2ePairingLoopPass123!"
_TTL_S = 120
_OBSERVE_S = 40.0
_READY_TIMEOUT_S = 15.0


def _signing_keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    priv_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(pub_raw).decode(), base64.b64encode(priv_raw).decode()


def _trust_key_fp(public_key_b64: str) -> str:
    from central_server.pairing.qr_signer import compute_trust_key_fp

    return compute_trust_key_fp(public_key_b64)


def _wait_until(predicate, *, timeout: float, interval: float = 0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s")


class FaultProxy:
    """Loopback TCP proxy in front of the relay, run on its own event loop thread.

    Every agent request opens a fresh connection (urllib sends
    ``Connection: close``; each WS dial is its own socket), so only the first
    request line of a connection is inspected. ``ws_mode`` decides what
    happens to ``/ws/pairing/`` upgrades:

    * ``pass``: forwarded untouched;
    * ``reject_502`` / ``reject_403``: answered by the proxy, never forwarded;
    * ``eof``: connection closed before any response;
    * ``swallow_once``: the first upgrade reaches the relay, the client gets
      the ``101`` headers but no frames after them (a lost ``ready``); later
      upgrades pass.
    """

    def __init__(self, upstream_port: int, ws_mode: str = "pass") -> None:
        self.upstream_port = upstream_port
        self.ws_mode = ws_mode
        self.port = _free_port()
        self.initiates: list[float] = []
        self.ws_attempts: list[float] = []
        self._swallowed = False
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._server: asyncio.base_events.Server | None = None
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)

    def start(self) -> "FaultProxy":
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._start(), self._loop).result(timeout=10)
        return self

    def stop(self) -> None:
        async def _close() -> None:
            if self._server is not None:
                self._server.close()

        try:
            asyncio.run_coroutine_threadsafe(_close(), self._loop).result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def counts(self) -> tuple[int, int]:
        with self._lock:
            return len(self.initiates), len(self.ws_attempts)

    async def _start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except Exception:
            writer.close()
            return
        request_line = head.split(b"\r\n", 1)[0]
        swallow = False
        with self._lock:
            now = time.monotonic()
            if request_line.startswith(b"POST /api/pairing/initiate"):
                self.initiates.append(now)
            is_pairing_ws = b" /ws/pairing/" in request_line
            if is_pairing_ws:
                self.ws_attempts.append(now)
                if self.ws_mode == "swallow_once" and not self._swallowed:
                    self._swallowed = True
                    swallow = True
        if is_pairing_ws and self.ws_mode in ("reject_502", "reject_403", "eof"):
            if self.ws_mode != "eof":
                status = "502 Bad Gateway" if self.ws_mode == "reject_502" else "403 Forbidden"
                writer.write(
                    f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n"
                    f"Connection: close\r\n\r\n".encode()
                )
                await writer.drain()
            writer.close()
            return
        try:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.upstream_port)
        except OSError:
            writer.close()
            return
        up_writer.write(head)
        await up_writer.drain()
        await asyncio.gather(
            self._pump(reader, up_writer),
            self._pump(up_reader, writer, swallow_after_headers=swallow),
            return_exceptions=True,
        )

    @staticmethod
    async def _pump(
        src: asyncio.StreamReader,
        dst: asyncio.StreamWriter,
        *,
        swallow_after_headers: bool = False,
    ) -> None:
        buffered = b""
        headers_done = False
        try:
            while True:
                chunk = await src.read(65536)
                if not chunk:
                    break
                if swallow_after_headers:
                    if headers_done:
                        continue
                    buffered += chunk
                    end = buffered.find(b"\r\n\r\n")
                    if end < 0:
                        continue
                    chunk = buffered[: end + 4]
                    headers_done = True
                dst.write(chunk)
                await dst.drain()
        finally:
            try:
                dst.close()
            except Exception:
                pass


@dataclass
class Relay:
    port: int
    public_key_b64: str
    client: MockMobileClient
    tmp: Path

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _boot_relay(request, xprocess, *, name: str, extra_env: dict[str, str]) -> Relay:
    tmp = Path(tempfile.mkdtemp(prefix=f"{name}-"))
    request.addfinalizer(lambda: shutil.rmtree(tmp, ignore_errors=True))
    port = _free_port()
    process_name = f"{name}_{port}"
    request.addfinalizer(lambda: _terminate(xprocess, process_name))
    public_key_b64, private_key_b64 = _signing_keypair()
    env = {
        **os.environ,
        "MOBAIDE_ENV": "test",
        "JWT_SECRET_KEY": secrets.token_urlsafe(32),
        "EMAIL_VERIFICATION_REQUIRED": "false",
        "ADMIN_SEED_EMAIL": E2E_ADMIN_EMAIL,
        "ADMIN_SEED_PASSWORD": E2E_ADMIN_PASSWORD,
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "DB_PATH": str(tmp / "central.db"),
        "MANIFEST_STORE_PATH": str(tmp / "manifest.json"),
        "MANIFEST_SIGNING_PUBLIC_KEY": public_key_b64,
        "MANIFEST_SIGNING_PRIVATE_KEY": private_key_b64,
        "PAIRING_QR_TOKEN_TTL_SECONDS": str(_TTL_S),
        "LOG_FORMAT": "console",
        "LOG_LEVEL": "INFO",
        "PYTHONUNBUFFERED": "1",
        **extra_env,
    }
    starter = _make_starter(
        args=[sys.executable, "-u", "-m", "central_server"],
        env=env,
        cwd=tmp,
        ready=_server_ready(port),
    )
    xprocess.ensure(process_name, starter)
    client = MockMobileClient(f"http://127.0.0.1:{port}")
    request.addfinalizer(client.close)
    client.register_and_login(_OWNER_EMAIL, _OWNER_PASSWORD)
    return Relay(port=port, public_key_b64=public_key_b64, client=client, tmp=tmp)


@pytest.fixture(scope="module")
def relay(request, xprocess) -> Relay:
    """Relay with the initiate cap disabled, so scenarios cannot starve each other."""
    return _boot_relay(
        request, xprocess, name="pairing_loop_relay",
        extra_env={"PAIRING_INITIATE_IP_HOURLY_CAP": "0"},
    )


@pytest.fixture(scope="module")
def capped_relay(request, xprocess) -> Relay:
    return _boot_relay(
        request, xprocess, name="pairing_loop_capped_relay",
        extra_env={"PAIRING_INITIATE_IP_HOURLY_CAP": "3"},
    )


@dataclass
class UnpairedAgent:
    prefix: Path
    log_path: Path
    proxy: FaultProxy

    def log_text(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def pending_nonce(self) -> str | None:
        path = self.prefix / "run" / "pending_pairing.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))["nonce"]
        except (OSError, ValueError, KeyError):
            return None


@pytest.fixture
def spawn_unpaired_agent(request, xprocess):
    """Boot a credential-less agent whose relay traffic runs through a ``FaultProxy``."""

    def spawn(
        relay: Relay,
        *,
        ws_mode: str = "pass",
        clock_offset_s: float = 0.0,
    ) -> UnpairedAgent:
        proxy = FaultProxy(relay.port, ws_mode).start()
        request.addfinalizer(proxy.stop)
        agent_port = _free_port()
        process_name = f"pairing_loop_agent_{agent_port}"
        prefix = relay.tmp / process_name
        prefix.mkdir(parents=True, exist_ok=True)
        request.addfinalizer(lambda: _terminate(xprocess, process_name))
        env = {
            **os.environ,
            "MOBAIDE_ENV": "test",
            "JWT_SECRET_KEY": secrets.token_urlsafe(32),
            "HOST": "127.0.0.1",
            "PORT": str(agent_port),
            "RELAY_SERVER_URL": f"ws://127.0.0.1:{proxy.port}",
            "RELAY_AGENT_ID": "",
            "RELAY_AGENT_TOKEN": "",
            "MOBAIDE_AGENT_TRUST_KEY": relay.public_key_b64,
            "MOBAIDE_AGENT_ALLOW_INSECURE_WS": "1",
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH": "1",
            "MOBAIDE_AGENT_PREFIX": str(prefix),
            "LOG_FORMAT": "console",
            "LOG_LEVEL": "INFO",
            "PYTHONUNBUFFERED": "1",
        }
        if clock_offset_s:
            # A sitecustomize shifts the agent's wall clock only; monotonic
            # time (asyncio timers, backoff sleeps) is untouched.
            shim = prefix / "clock_shim"
            shim.mkdir()
            (shim / "sitecustomize.py").write_text(
                "import time as _t\n"
                "_real = _t.time\n"
                f"_t.time = lambda: _real() + {float(clock_offset_s)!r}\n",
                encoding="utf-8",
            )
            env["PYTHONPATH"] = os.pathsep.join(
                p for p in (str(shim), os.environ.get("PYTHONPATH", "")) if p
            )
        starter = _make_starter(
            args=[sys.executable, "-u", "-m", "mobaide"],
            env=env,
            cwd=relay.tmp,
            ready=_agent_ready(agent_port),
        )
        _, log_path = xprocess.ensure(process_name, starter)
        return UnpairedAgent(prefix=prefix, log_path=Path(log_path), proxy=proxy)

    return spawn


def _claim(relay: Relay, nonce: str) -> str:
    resp = relay.client._http.post(
        "/api/pairing/claim",
        json={
            "nonce": nonce,
            "trust_key_fp": _trust_key_fp(relay.public_key_b64),
            "mobile_device_id": str(uuid.uuid4()),
            "platform": "e2e",
            "friendly_name": "e2e-pairing-loop",
        },
        headers=relay.client._bearer(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["agent_id"]


def _wait_online(relay: Relay, agent_id: str, timeout: float = 30.0) -> None:
    def online() -> bool:
        return any(
            a["agent_id"] == agent_id and a.get("online")
            for a in relay.client.list_agents()
        )

    _wait_until(online, timeout=timeout)


def _gaps(stamps: list[float]) -> list[float]:
    return [round(b - a, 1) for a, b in zip(stamps, stamps[1:])]


def test_healthy_pairing_initiates_once_and_comes_online(relay, spawn_unpaired_agent):
    agent = spawn_unpaired_agent(relay)

    nonce = _wait_until(agent.pending_nonce, timeout=30)
    agent_id = _claim(relay, nonce)
    _wait_online(relay, agent_id)

    initiates, ws_attempts = agent.proxy.counts()
    assert initiates == 1
    assert ws_attempts == 1
    assert "pairing_monitor.paired" in agent.log_text()


@pytest.mark.parametrize("ws_mode", ["reject_502", "eof"])
def test_ws_upgrade_failure_reuses_the_nonce(relay, spawn_unpaired_agent, ws_mode):
    agent = spawn_unpaired_agent(relay, ws_mode=ws_mode)

    time.sleep(_OBSERVE_S)

    initiates, ws_attempts = agent.proxy.counts()
    gaps = _gaps(agent.proxy.ws_attempts)
    assert initiates == 1, f"initiates={initiates} ws_gaps={gaps}"
    assert ws_attempts >= 4, f"ws_gaps={gaps}"
    assert gaps == sorted(gaps), f"backoff must not shrink: {gaps}"
    assert agent.pending_nonce() is None


def test_ws_upgrade_always_403_costs_one_extra_initiate(relay, spawn_unpaired_agent):
    agent = spawn_unpaired_agent(relay, ws_mode="reject_403")

    time.sleep(_OBSERVE_S)

    initiates, ws_attempts = agent.proxy.counts()
    gaps = _gaps(agent.proxy.ws_attempts)
    assert initiates == 2, f"initiates={initiates} ws_gaps={gaps}"
    assert ws_attempts >= 4, f"ws_gaps={gaps}"
    assert gaps == sorted(gaps), f"backoff must not shrink: {gaps}"


def test_lost_ready_frame_recovers_with_one_fresh_nonce(relay, spawn_unpaired_agent):
    agent = spawn_unpaired_agent(relay, ws_mode="swallow_once")

    nonce = _wait_until(agent.pending_nonce, timeout=_READY_TIMEOUT_S + 30)
    agent_id = _claim(relay, nonce)
    _wait_online(relay, agent_id)

    initiates, ws_attempts = agent.proxy.counts()
    log = agent.log_text()
    assert initiates == 2, f"initiates={initiates} ws_gaps={_gaps(agent.proxy.ws_attempts)}"
    assert ws_attempts == 3
    assert "pairing_ws.ready_timeout" in log
    assert "403" in log
    assert "pairing_monitor.paired" in log


def test_clock_ahead_of_relay_backs_off_and_warns(relay, spawn_unpaired_agent):
    agent = spawn_unpaired_agent(relay, clock_offset_s=_TTL_S + 180)

    time.sleep(_OBSERVE_S)

    initiates, _ = agent.proxy.counts()
    gaps = _gaps(agent.proxy.initiates)
    assert "pairing_monitor.clock_skew" in agent.log_text()
    # The incident cadence was one initiate per ~4 s (~10 in this window).
    assert 2 <= initiates <= 5, f"initiates={initiates} gaps={gaps}"
    assert gaps == sorted(gaps), f"backoff must not shrink: {gaps}"


def test_relay_caps_initiates_per_ip(capped_relay, spawn_unpaired_agent, tmp_path):
    for _ in range(3):
        resp = httpx.post(f"{capped_relay.base}/api/pairing/initiate", json={}, timeout=10)
        assert resp.status_code == 200, resp.text

    refused = httpx.post(f"{capped_relay.base}/api/pairing/initiate", json={}, timeout=10)
    assert refused.status_code == 429
    detail = refused.json()["detail"]
    assert detail["error"] == "pairing_initiate_rate_limited"
    assert int(refused.headers["Retry-After"]) == detail["retry_after_seconds"] > 0

    prefix = tmp_path / "pair_qr_prefix"
    env_file = prefix / "mobaide_agent" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(f"RELAY_SERVER_URL=ws://127.0.0.1:{capped_relay.port}\n", encoding="utf-8")
    cli = subprocess.run(
        [sys.executable, "-m", "mobaide", "pair-qr", "--print", "--prefix", str(prefix)],
        env={
            **os.environ,
            "MOBAIDE_AGENT_TRUST_KEY": capped_relay.public_key_b64,
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cli.returncode == 3, cli.stderr
    assert "rate-limiting pairing requests from this network" in cli.stderr
    assert re.search(r"Retry in \d+s\.", cli.stderr), cli.stderr

    agent = spawn_unpaired_agent(capped_relay)
    time.sleep(20.0)
    initiates, ws_attempts = agent.proxy.counts()
    gaps = _gaps(agent.proxy.initiates)
    assert ws_attempts == 0
    assert "pairing_monitor.initiate_failed" in agent.log_text()
    assert "429" in agent.log_text()
    assert initiates <= 5, f"initiates={initiates} gaps={gaps}"
    assert gaps == sorted(gaps), f"backoff must not shrink: {gaps}"
