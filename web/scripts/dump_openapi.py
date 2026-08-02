#!/usr/bin/env python3
"""Dump both OpenAPI schemas (central server + agent) for the web workspace.

Builds each FastAPI app in-process and serializes ``app.openapi()`` — no
running server and no environment prerequisites beyond
``pip install -e 'mobaide_agent[dev]' -e 'central_server[dev]'``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = WEB_DIR.parent
OPENAPI_DIR = WEB_DIR / "openapi"

# info.version tracks the installed dist versions, which bump on every
# release — normalize to a sentinel so releases don't churn the schemas.
VERSION_SENTINEL = "0.0.0"


def build_schemas() -> dict[str, dict]:
    # The central app resolves legal/privacy.md, templates/ and static/
    # relative to cwd (or the editable-install source tree).
    os.chdir(REPO_ROOT)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        from central_server.app import create_app as create_central_app
        from central_server.config import CentralServerConfig

        central = create_central_app(
            CentralServerConfig(
                jwt_secret_key="dump-openapi",
                db_path=str(tmp_path / "central.db"),
                manifest_store_path=str(tmp_path / "agent_manifest.json"),
                log_format="console",
            )
        )

        from mobaide.agent.app import create_app as create_agent_app
        from mobaide.agent.config import AgentConfig

        # create_app writes users.json/projects.json when absent — point the
        # store paths at the tempdir. AgentConfig's model_config has
        # env_file=None, so direct construction never reads a developer .env.
        agent = create_agent_app(
            AgentConfig(
                jwt_secret_key="dump-openapi",
                user_store_path=str(tmp_path / "users.json"),
                projects_store_path=str(tmp_path / "projects.json"),
            )
        )

        schemas = {
            "central-server.json": central.openapi(),
            "agent.json": agent.openapi(),
        }

    for schema in schemas.values():
        schema["info"]["version"] = VERSION_SENTINEL
        _uniquify_operation_ids(schema)
    return schemas


_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def _uniquify_operation_ids(schema: dict) -> None:
    """Make duplicated operationIds unique and deterministic.

    FastAPI gives every method of a multi-method route (the relay's 5-method
    ``/proxy/{agent_id}/{path:path}`` catch-all) the same operationId, which
    is spec-invalid and makes openapi-typescript emit duplicate identifiers.
    Worse, the shared id embeds ``next(iter(route.methods))`` — a set — so
    its method suffix changes from process to process. Strip that suffix and
    append each operation's actual method instead.
    """
    counts: dict[str, int] = {}
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            if isinstance(operation, dict) and "operationId" in operation:
                op_id = operation["operationId"]
                counts[op_id] = counts.get(op_id, 0) + 1
    for operations in schema.get("paths", {}).values():
        for method, operation in operations.items():
            if isinstance(operation, dict) and "operationId" in operation:
                op_id = operation["operationId"]
                if counts[op_id] > 1:
                    base = op_id
                    for suffix in _HTTP_METHODS:
                        if base.endswith(f"_{suffix}"):
                            base = base[: -len(suffix) - 1]
                            break
                    operation["operationId"] = f"{base}_{method}"


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed schemas differ instead of writing",
    )
    check = parser.parse_args().check

    stale: list[str] = []
    for name, schema in build_schemas().items():
        target = OPENAPI_DIR / name
        text = render(schema)
        if check:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            OPENAPI_DIR.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print(f"wrote {target.relative_to(REPO_ROOT)}")

    if stale:
        print(
            f"stale schemas: {', '.join(stale)} — run `npm run schema:dump` in web/",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
