"""Entry point: python -m claude_mobile"""
from __future__ import annotations

import uvicorn

from claude_mobile.server.app import create_app


def main() -> None:
    app = create_app()
    uvicorn.run(
        app,
        host=app.state.config.host,
        port=app.state.config.port,
    )


if __name__ == "__main__":
    main()
