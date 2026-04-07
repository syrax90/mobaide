from __future__ import annotations

import asyncio

import pytest

from claude_mobile.core.models import SessionConfig, SessionEvent, SessionState
from claude_mobile.core.session import TerminalSession
from tests.conftest import IS_WINDOWS


@pytest.mark.timeout(15)
class TestTerminalSessionLifecycle:
    async def test_start_and_stop(self, session_config: SessionConfig) -> None:
        session = TerminalSession(session_config)
        await session.start()

        assert session.state == SessionState.RUNNING
        assert session.pid is not None
        assert session.session_id == "test-session"

        await session.stop()
        assert session.state == SessionState.STOPPED

    async def test_write_and_read_output(self, session_config: SessionConfig) -> None:
        session = TerminalSession(session_config)
        await session.start()

        try:
            # Wait for shell to initialize
            await asyncio.sleep(0.5)

            if IS_WINDOWS:
                await session.write(b"echo SESSIONTEST456\r\n")
            else:
                await session.write(b"echo SESSIONTEST456\n")

            # Drain events until we see our marker
            output = b""
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    event: SessionEvent = await asyncio.wait_for(
                        session.event_queue.get(),
                        timeout=1.0,
                    )
                    if event.event_type == "output" and event.data:
                        output += event.data
                    if b"SESSIONTEST456" in output:
                        break
                except asyncio.TimeoutError:
                    continue

            assert b"SESSIONTEST456" in output
        finally:
            await session.stop()

    async def test_scrollback_accumulates(
        self, session_config: SessionConfig
    ) -> None:
        session = TerminalSession(session_config)
        await session.start()

        try:
            await asyncio.sleep(0.5)

            for i in range(5):
                if IS_WINDOWS:
                    await session.write(f"echo line{i}\r\n".encode())
                else:
                    await session.write(f"echo line{i}\n".encode())

            # Give time for output to be read
            await asyncio.sleep(1.0)

            scrollback = session.get_scrollback()
            assert len(scrollback) > 0
        finally:
            await session.stop()

    async def test_scrollback_max_size(self) -> None:
        config = SessionConfig(
            session_id="small-buffer",
            command=["cmd.exe"] if IS_WINDOWS else ["/bin/bash"],
            scrollback_size=3,
        )
        session = TerminalSession(config)
        await session.start()

        try:
            await asyncio.sleep(0.5)

            for i in range(10):
                if IS_WINDOWS:
                    await session.write(f"echo overflow{i}\r\n".encode())
                else:
                    await session.write(f"echo overflow{i}\n".encode())

            await asyncio.sleep(1.5)

            scrollback = session.get_scrollback()
            # Deque maxlen=3 means at most 3 entries
            assert len(scrollback) <= 3
        finally:
            await session.stop()

    async def test_resize(self, session_config: SessionConfig) -> None:
        session = TerminalSession(session_config)
        await session.start()

        try:
            await session.resize(40, 120)
        finally:
            await session.stop()

    async def test_crash_detection(
        self, short_lived_config: SessionConfig
    ) -> None:
        """A short-lived process should trigger CRASHED state."""
        session = TerminalSession(short_lived_config)
        await session.start()

        # Wait for the short-lived process to exit and reader loop to detect it
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            if session.state in (SessionState.CRASHED, SessionState.STOPPED):
                break
            await asyncio.sleep(0.2)

        assert session.state in (SessionState.CRASHED, SessionState.STOPPED)

    async def test_started_event_emitted(
        self, session_config: SessionConfig
    ) -> None:
        session = TerminalSession(session_config)
        await session.start()

        try:
            event = await asyncio.wait_for(session.event_queue.get(), timeout=2.0)
            assert event.event_type == "started"
            assert event.session_id == "test-session"
        finally:
            await session.stop()

    async def test_stop_is_idempotent(
        self, session_config: SessionConfig
    ) -> None:
        session = TerminalSession(session_config)
        await session.start()
        await session.stop()
        await session.stop()  # Should not raise
        assert session.state == SessionState.STOPPED
