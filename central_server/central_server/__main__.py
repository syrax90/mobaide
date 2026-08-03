"""Entry point: python -m central_server"""
from __future__ import annotations

import uvicorn

from central_server.app import create_app


def main() -> None:
    app = create_app()
    # access_log=False suppresses uvicorn's default access-log line, which
    # would otherwise write the full request line (including any query
    # string) for every HTTP call. The in-app structured middleware in
    # ``app.py`` already logs every request at INFO with method/path/status
    # while deliberately omitting query strings — important because the
    # public ``/portal/cancel-deletion?token=…`` URL carries a single-use
    # cancellation token that must not be persisted to operational logs.
    uvicorn.run(
        app,
        host=app.state.config.host,
        port=app.state.config.port,
        access_log=False,
        # Pinned rather than "auto" so ws_per_message_deflate below is
        # guaranteed to reach an implementation that honours it, and so a
        # future uvicorn auto-resolution change cannot silently regress the
        # per-connection memory profile. "websockets-sansio" is what "auto"
        # resolves to on current uvicorn (the "websockets" legacy impl is
        # deprecated and rides websockets.legacy, which is removal-tracked);
        # uvicorn[standard] guarantees the websockets package is installed.
        ws="websockets-sansio",
        # permessage-deflate keeps a zlib context per connection (~64 KiB at
        # default window bits vs ~14 KiB without). Relay payloads are small
        # PTY chunks and JSON envelopes, so compression buys little; the
        # memory goes to connection headroom instead (load model:
        # ~10k sockets at Tier B).
        ws_per_message_deflate=False,
    )


if __name__ == "__main__":
    main()
