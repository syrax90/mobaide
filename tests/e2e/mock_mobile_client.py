"""Headless mock-mobile client for the cross-process E2E harness.

Speaks the same REST + WebSocket protocol the Android app uses, against the
**central server only** — never the agent directly. The server validates the
mobile JWT and relays over the already-authenticated agent tunnel, so this
client authenticates exclusively to the server (see the relay-path analysis in
the issue: proxied requests and CH_SESSION_IO frames bypass agent-local auth).

Frame constants are imported from the live agent module rather than hardcoded,
so the harness can never drift from the wire contract pinned by
``tests/test_protocol_integrity.py``.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx
import websockets

from mobaide.agent.ws.session_io import (
    FRAME_EXECUTE,
    FRAME_INPUT,
    FRAME_OUTPUT,
    FRAME_REPLAY_END,
    FRAME_SUBMIT,
)
from mobaide.relay.envelope import PROTOCOL_VERSION

__all__ = [
    "MockMobileClient",
    "FRAME_INPUT",
    "FRAME_SUBMIT",
    "FRAME_EXECUTE",
    "FRAME_OUTPUT",
    "PROTOCOL_VERSION",
]


class MockMobileClient:
    """Drives one disposable mobile identity against a running central server."""

    def __init__(self, server_base: str, *, http_timeout: float = 30.0) -> None:
        self.server_base = server_base.rstrip("/")
        # http://host:port -> ws://host:port ; https -> wss
        self.ws_base = "ws" + self.server_base[len("http"):]
        self._http = httpx.Client(base_url=self.server_base, timeout=http_timeout)
        self.access_token: str | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MockMobileClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def health(self, *, token: str | None = None) -> dict:
        """GET /api/health. Attaches a Bearer token (explicit or the logged-in
        owner's) when available, since the agent counts are admin-gated."""
        token = token if token is not None else self.access_token
        headers = self._bearer(token) if token else {}
        resp = self._http.get("/api/health", headers=headers)
        resp.raise_for_status()
        return resp.json()

    def login(self, email: str, password: str) -> str:
        """Log in and return the access token without touching ``self.access_token``.

        Used to mint a token for an identity distinct from the logged-in
        owner (e.g. an admin, for the health-count-gated readiness poll) —
        agent-ownership endpoints reject admin tokens, so the owner identity
        driven by ``register_and_login``/``access_token`` must stay untouched.
        """
        resp = self._http.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError(f"no access_token from login: {resp.json()!r}")
        return token

    def register_and_login(self, email: str, password: str) -> str:
        """Create the owner user (verification disabled in test mode) and keep its dashboard JWT."""
        resp = self._http.post(
            "/api/auth/register", json={"email": email, "password": password}
        )
        token = resp.json().get("access_token") if resp.status_code == 200 else None
        if not token:
            # Already registered (re-run) or verification-gated: fall back to login.
            resp = self._http.post(
                "/api/auth/login", json={"email": email, "password": password}
            )
            resp.raise_for_status()
            token = resp.json().get("access_token")
        if not token:
            raise RuntimeError(f"no access_token from register/login: {resp.json()!r}")
        self.access_token = token
        return token

    def _bearer(self, token: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token or self.access_token}"}

    def create_agent(self, name: str = "e2e-agent") -> tuple[str, str]:
        """Register an agent owned by the logged-in user; returns (agent_id, raw_token)."""
        resp = self._http.post("/api/agents", json={"name": name}, headers=self._bearer())
        resp.raise_for_status()
        body = resp.json()
        return body["agent_id"], body["token"]

    def list_agents(self) -> list[dict]:
        resp = self._http.get("/api/agents", headers=self._bearer())
        resp.raise_for_status()
        return resp.json()

    def delete_agent(self, agent_id: str, *, uninstall: bool = False) -> None:
        """DELETE /api/agents/{id}; ``uninstall`` also orders the agent to remove itself."""
        resp = self._http.delete(
            f"/api/agents/{agent_id}",
            params={"uninstall": "true" if uninstall else "false"},
            headers=self._bearer(),
        )
        resp.raise_for_status()

    def wait_agents_online(
        self,
        minimum: int = 1,
        *,
        timeout: float = 30.0,
        interval: float = 0.25,
        token: str | None = None,
    ) -> dict:
        """Poll /api/health until ``agents_online >= minimum`` (the correct cross-process gate).

        ``token`` must be an admin token — the counts are admin-gated, so an
        anonymous or non-admin poll would never see ``agents_online`` rise
        and would time out even once the agent tunnel is actually up.
        """
        deadline = time.monotonic() + timeout
        last: dict | None = None
        while time.monotonic() < deadline:
            try:
                last = self.health(token=token)
                if last.get("agents_online", 0) >= minimum:
                    return last
            except httpx.HTTPError:
                pass
            time.sleep(interval)
        raise TimeoutError(
            f"agents_online < {minimum} after {timeout}s (last health={last!r})"
        )

    def mint_mobile_token(
        self, agent_id: str, *, device_name: str = "e2e-mock-device"
    ) -> str:
        """Mint a mobile access JWT for ``agent_id`` (device id is a fresh UUID)."""
        resp = self._http.post(
            f"/api/agents/{agent_id}/token",
            json={"mobile_device_id": str(uuid.uuid4()), "mobile_device_name": device_name},
            headers=self._bearer(),
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def create_shell_session(
        self, agent_id: str, mobile_token: str, **overrides
    ) -> str:
        """Create a SHELL session over the server proxy; returns ``session_id``.

        An empty body yields a non-managed shell session: the agent's FakePtyBackend
        is used and no claude/codex CLI binary is resolved.
        """
        resp = self._http.post(
            f"/proxy/{agent_id}/api/sessions",
            json=dict(overrides),
            headers=self._bearer(mobile_token),
        )
        resp.raise_for_status()
        return resp.json()["session_id"]

    def create_project(
        self, agent_id: str, mobile_token: str, name: str, path: str
    ) -> dict:
        """Create a project on the agent over the server proxy."""
        resp = self._http.post(
            f"/proxy/{agent_id}/api/projects",
            json={"name": name, "path": path},
            headers=self._bearer(mobile_token),
        )
        resp.raise_for_status()
        return resp.json()

    def list_projects(self, agent_id: str, mobile_token: str) -> list[dict]:
        """List the agent's projects over the server proxy."""
        resp = self._http.get(
            f"/proxy/{agent_id}/api/projects",
            headers=self._bearer(mobile_token),
        )
        resp.raise_for_status()
        return resp.json()

    def reorder_projects(
        self, agent_id: str, mobile_token: str, names: list[str]
    ) -> list[dict]:
        """Reorder the agent's projects via ``PATCH`` over the server proxy."""
        resp = self._http.patch(
            f"/proxy/{agent_id}/api/projects/order",
            json={"names": names},
            headers=self._bearer(mobile_token),
        )
        resp.raise_for_status()
        return resp.json()

    def proxy_request(
        self,
        agent_id: str,
        mobile_token: str,
        method: str,
        path: str,
        body: dict | None = None,
    ) -> httpx.Response:
        """Call ``/<path>`` on the agent over the server proxy and return the
        raw response, refusals included (no ``raise_for_status``)."""
        return self._http.request(
            method,
            f"/proxy/{agent_id}/{path.lstrip('/')}",
            json=body,
            headers=self._bearer(mobile_token),
        )

    def _session_ws_url(self, agent_id: str, session_id: str, mobile_token: str) -> str:
        return (
            f"{self.ws_base}/relay/ws/session/{agent_id}/{session_id}?token={mobile_token}"
        )

    async def drive_session(
        self,
        agent_id: str,
        session_id: str,
        mobile_token: str,
        *,
        frame: bytes,
        expect: bytes,
        attach_timeout: float = 15.0,
        io_timeout: float = 15.0,
    ) -> bytes:
        """Attach to a relayed PTY session, send one frame, return output until ``expect`` appears.

        Consumes the binary attach handshake (``s`` -> ``b`` -> ``o*`` -> ``e``), sends
        ``frame`` (e.g. ``FRAME_INPUT + b"hi"``), then accumulates ``FRAME_OUTPUT`` payloads
        until they contain ``expect``. Raises ``TimeoutError`` if the handshake or echo
        does not arrive in time.
        """
        url = self._session_ws_url(agent_id, session_id, mobile_token)
        async with websockets.connect(
            url, open_timeout=attach_timeout, max_size=8 * 1024 * 1024
        ) as ws:
            await self._await_replay_end(ws, attach_timeout)
            await ws.send(frame)
            return await self._collect_output(ws, expect, io_timeout)

    async def read_sessions_list(
        self, agent_id: str, mobile_token: str, *, timeout: float = 15.0
    ) -> list[dict]:
        """Open the JSON sessions-list relay channel and return the first snapshot array."""
        url = f"{self.ws_base}/relay/ws/sessions/{agent_id}?token={mobile_token}"
        async with websockets.connect(url, open_timeout=timeout) as ws:

            async def _loop() -> list[dict]:
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, (bytes, bytearray)):
                        msg = msg.decode("utf-8")
                    data = json.loads(msg)
                    if isinstance(data, list):
                        return data

            return await asyncio.wait_for(_loop(), timeout=timeout)

    def user_events_url(self) -> str:
        """The logged-in owner's user-scoped event socket (``/ws/user-events``)."""
        return f"{self.ws_base}/ws/user-events?token={self.access_token}"

    @staticmethod
    async def attention_until(
        ws, session_id: str, *, timeout: float = 15.0
    ) -> list[dict]:
        """Read ``session.attention`` payloads off a user-events socket up to
        and including the first one for ``session_id``; return them all.

        Other event types are skipped. The payloads for other sessions are
        returned too, so a test can assert that a session stayed quiet.
        """
        seen: list[dict] = []

        async def _loop() -> list[dict]:
            while True:
                msg = await ws.recv()
                if isinstance(msg, (bytes, bytearray)):
                    msg = msg.decode("utf-8")
                envelope = json.loads(msg)
                if envelope.get("type") != "session.attention":
                    continue
                payload = envelope.get("payload") or {}
                seen.append(payload)
                if payload.get("session_id") == session_id:
                    return seen

        return await asyncio.wait_for(_loop(), timeout=timeout)

    @staticmethod
    async def _await_replay_end(ws, timeout: float) -> None:
        async def _loop() -> None:
            while True:
                msg = await ws.recv()
                if isinstance(msg, str) or not msg:
                    continue
                if msg[:1] == FRAME_REPLAY_END:
                    return

        await asyncio.wait_for(_loop(), timeout=timeout)

    @staticmethod
    async def _collect_output(ws, expect: bytes, timeout: float) -> bytes:
        buf = bytearray()

        async def _loop() -> bytes:
            while True:
                msg = await ws.recv()
                if isinstance(msg, str) or not msg:
                    continue
                if msg[:1] == FRAME_OUTPUT:
                    buf.extend(msg[1:])
                    if expect in buf:
                        return bytes(buf)

        return await asyncio.wait_for(_loop(), timeout=timeout)
