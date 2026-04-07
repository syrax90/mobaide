"""Tests for the @log_call decorator.

The decorator emits records via structlog. Depending on whether
``configure_logging`` has been invoked elsewhere in the test session,
the records may go through a stdlib logger (caplog) or directly to stdout
(capsys). We check both sources so the test is robust either way.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from claude_mobile.server.logging.decorators import log_call


def _all_output(capsys, caplog) -> str:
    captured = capsys.readouterr()
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    return captured.out + "\n" + log_text


def test_sync_ok(capsys, caplog):
    @log_call(name="test.sync_ok")
    def add(a, b):
        return a + b

    with caplog.at_level(logging.DEBUG):
        assert add(2, 3) == 5
    out = _all_output(capsys, caplog)
    assert "call.start" in out
    assert "call.end" in out
    assert "test.sync_ok" in out


def test_sync_error(capsys, caplog):
    @log_call(name="test.sync_error")
    def boom():
        raise ValueError("nope")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ValueError):
            boom()
    out = _all_output(capsys, caplog)
    assert "call.end" in out
    assert "ValueError" in out


def test_async_ok(capsys, caplog):
    @log_call(name="test.async_ok")
    async def aplus(a, b):
        await asyncio.sleep(0)
        return a + b

    with caplog.at_level(logging.DEBUG):
        assert asyncio.run(aplus(1, 2)) == 3
    out = _all_output(capsys, caplog)
    assert "call.start" in out
    assert "call.end" in out
