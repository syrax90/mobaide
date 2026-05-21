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
    )


if __name__ == "__main__":
    main()
