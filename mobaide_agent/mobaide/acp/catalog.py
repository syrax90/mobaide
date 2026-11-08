"""Lists the sessions an ACP agent has stored on this host.

This is what lets a user continue, in the chat UI, a session that was
started outside Mobaide (for example by running ``claude`` in a terminal):
``session/list`` finds it, and a chat session then opens it with
``session/load``. One connection per agent kind is kept for a short idle
period so that paging through the list does not respawn the agent.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any

from mobaide import __version__

from .agents import ChatAgentSpec, ChatAgentUnavailableError
from .install import AdapterInstallError
from .rpc import ERR_METHOD_NOT_FOUND, AcpConnection, AcpProcessError, AcpRpcError

logger = logging.getLogger(__name__)

IDLE_SECONDS = 60.0
INIT_TIMEOUT = 120.0
LIST_TIMEOUT = 30.0
PAGE_MAX = 200
TITLE_MAX = 200


class ChatCatalogError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class _Lister:
    def __init__(self, conn: AcpConnection, can_list: bool) -> None:
        self.conn = conn
        self.can_list = can_list
        self.idle_handle: asyncio.TimerHandle | None = None


class ChatCatalog:
    def __init__(self, specs: Mapping[str, ChatAgentSpec]) -> None:
        self._specs = specs
        self._listers: dict[str, _Lister] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def list_sessions(
        self, kind: str, *, cwd: str | None, cursor: str | None
    ) -> dict[str, Any]:
        spec = self._specs.get(kind)
        if spec is None or not spec.supports_resume:
            raise ChatCatalogError(404, f"Chat sessions are not available for {kind}")
        if cwd is not None and not os.path.isabs(cwd):
            raise ChatCatalogError(400, "cwd must be an absolute path")
        lock = self._locks.setdefault(kind, asyncio.Lock())
        async with lock:
            lister = await self._lister(kind, spec)
            params: dict[str, Any] = {}
            if cwd:
                params["cwd"] = cwd
            if cursor:
                params["cursor"] = cursor
            try:
                if not lister.can_list:
                    raise ChatCatalogError(409, f"{spec.display_name} cannot list its sessions")
                result = await lister.conn.request("session/list", params, LIST_TIMEOUT)
            except AcpRpcError as exc:
                raise ChatCatalogError(
                    400 if exc.code == -32602 else 502, exc.message
                ) from None
            except (AcpProcessError, TimeoutError) as exc:
                await self._drop(kind)
                raise ChatCatalogError(502, str(exc) or "The agent did not answer") from None
            finally:
                self._arm_idle(kind)
        return _normalize_page(result)

    async def close(self) -> None:
        for kind in list(self._listers):
            await self._drop(kind)

    async def _lister(self, kind: str, spec: ChatAgentSpec) -> _Lister:
        existing = self._listers.get(kind)
        if existing is not None:
            # A dead lister's idle timer would otherwise drop its replacement.
            if existing.idle_handle is not None:
                existing.idle_handle.cancel()
                existing.idle_handle = None
            if existing.conn.alive:
                return existing
            self._listers.pop(kind, None)
            await existing.conn.close(grace=0.5)
        try:
            plan = await spec.catalog_plan()  # type: ignore[attr-defined]
        except ChatAgentUnavailableError as exc:
            raise ChatCatalogError(503, str(exc)) from None
        except AdapterInstallError as exc:
            raise ChatCatalogError(502, str(exc)) from None

        async def no_requests(method: str, params: Any) -> Any:
            raise AcpRpcError(ERR_METHOD_NOT_FOUND, f"Method not found: {method}")

        conn = AcpConnection(
            plan.argv,
            cwd=plan.process_cwd or os.path.expanduser("~"),
            env=plan.env,
            on_notification=lambda _m, _p: None,
            on_request=no_requests,
            name=f"acp-catalog-{kind}",
        )
        try:
            await conn.start()
            init = await conn.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "mobaide", "title": "Mobaide", "version": __version__},
                },
                INIT_TIMEOUT,
            )
        except (OSError, AcpProcessError, AcpRpcError, TimeoutError) as exc:
            await conn.close(grace=0.5)
            raise ChatCatalogError(502, f"Could not start {spec.display_name}: {exc}") from None
        except asyncio.CancelledError:
            await asyncio.shield(conn.close(grace=0.5))
            raise
        caps = init.get("agentCapabilities") if isinstance(init, dict) else None
        session_caps = caps.get("sessionCapabilities") if isinstance(caps, dict) else None
        can_list = isinstance(session_caps, dict) and session_caps.get("list") is not None
        lister = _Lister(conn, can_list and bool(caps.get("loadSession")))  # type: ignore[union-attr]
        self._listers[kind] = lister
        return lister

    def _arm_idle(self, kind: str) -> None:
        lister = self._listers.get(kind)
        if lister is None:
            return
        if lister.idle_handle is not None:
            lister.idle_handle.cancel()
        loop = asyncio.get_running_loop()
        lister.idle_handle = loop.call_later(
            IDLE_SECONDS, lambda: asyncio.ensure_future(self._drop(kind))
        )

    async def _drop(self, kind: str) -> None:
        lister = self._listers.pop(kind, None)
        if lister is None:
            return
        if lister.idle_handle is not None:
            lister.idle_handle.cancel()
        await lister.conn.close(grace=1.0)


def _normalize_page(result: Any) -> dict[str, Any]:
    sessions: list[dict[str, Any]] = []
    raw = result.get("sessions") if isinstance(result, dict) else None
    if isinstance(raw, list):
        for entry in raw[:PAGE_MAX]:
            if not isinstance(entry, dict):
                continue
            sid = entry.get("sessionId")
            cwd = entry.get("cwd")
            if not isinstance(sid, str) or not sid or not isinstance(cwd, str):
                continue
            title = entry.get("title")
            updated = entry.get("updatedAt")
            sessions.append(
                {
                    "session_id": sid,
                    "cwd": cwd,
                    "title": " ".join(title.split())[:TITLE_MAX] if isinstance(title, str) else None,
                    "updated_at": updated if isinstance(updated, str) else None,
                    "open_session_id": None,
                }
            )
    next_cursor = result.get("nextCursor") if isinstance(result, dict) else None
    return {
        "sessions": sessions,
        "next_cursor": next_cursor if isinstance(next_cursor, str) and next_cursor else None,
    }
