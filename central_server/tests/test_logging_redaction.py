from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
import uvicorn

import central_server.__main__ as entrypoint
from central_server.logging_setup import (
    REDACTED,
    _scrub_string,
    configure_logging,
    shutdown_logging,
)

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEifQ.c2lnbmF0dXJlLWJ5dGVz"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("/relay/ws/events/a1?token=" + FAKE_JWT, FAKE_JWT),
        ("/portal/cancel-deletion?token=opaque-123&next=1", "opaque-123"),
        ("/ws/user-events?device_id=D1&token=abc.def", "abc.def"),
        ("/relay/ws/session/a1/s1?ticket=t-9f8e", "t-9f8e"),
    ],
)
def test_scrub_string_hides_url_secrets(raw: str, secret: str) -> None:
    scrubbed = _scrub_string(raw)
    assert secret not in scrubbed
    assert REDACTED in scrubbed


def test_uvicorn_websocket_handshake_line_is_redacted(monkeypatch, capsys) -> None:
    """The handshake line uvicorn logs must reach the redacting pipeline.

    Runs uvicorn's own logging setup with the exact kwargs ``main()`` passes,
    since uvicorn's default dictConfig is what used to bypass redaction.
    """
    captured: dict = {}
    fake_app = SimpleNamespace(
        state=SimpleNamespace(config=SimpleNamespace(host="127.0.0.1", port=0))
    )
    monkeypatch.setattr(entrypoint, "create_app", lambda: fake_app)
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda app, **kw: captured.update(kw)
    )
    entrypoint.main()

    configure_logging(SimpleNamespace(log_format="json", log_level="INFO"))
    try:
        uvicorn.Config(app=fake_app, **captured)
        logging.getLogger("uvicorn.error").info(
            '%s - "WebSocket %s" %s',
            "203.0.113.7:0",
            f"/relay/ws/events/a1?token={FAKE_JWT}",
            "[accepted]",
        )
    finally:
        shutdown_logging()

    lines = [ln for ln in capsys.readouterr().out.splitlines() if "WebSocket" in ln]
    assert len(lines) == 1
    assert FAKE_JWT not in lines[0]
    record = json.loads(lines[0])
    assert REDACTED in record["event"]
