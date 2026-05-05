"""Agent configuration loaded via ``pydantic-settings``.

Reads from process environment variables; an explicit ``env_file`` path may
be passed to ``AgentConfig.from_env()`` so the loader does NOT depend on the
process CWD (which on Windows breaks the pre-CLA-48 ``load_dotenv()`` call).

The ``init-env`` and ``print-template`` CLI verbs live at the bottom of this
file; install scripts invoke them via ``python -m mobaide.agent.config``.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mobaide.linear.config import AutoScanConfig
from mobaide.relay.config import RelayConfig


_LOG_SAMPLE_WS_TRUTHY = frozenset({"1", "true", "yes", "on"})


class AgentConfig(BaseSettings):
    """Agent configuration loaded from environment variables.

    Required fields raise at construction; optional fields default to
    documented sensible values (matches the historical ``from_env``
    classmethod).
    """

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 0
    jwt_refresh_token_expire_minutes: int = 0
    user_store_path: str = "users.json"
    projects_store_path: str = "projects.json"
    host: str = "0.0.0.0"
    port: int = 8000
    max_sessions: int = 16
    session_disconnect_timeout: int = 300
    log_level: str = "INFO"
    log_format: str = "json"
    audit_log_path: str | None = None
    log_sample_ws: bool = False
    # First-boot seed for ``ProjectsStore``. Read here (instead of via
    # ``os.getenv`` inside the store) because pydantic-settings populates the
    # settings instance from `.env` but does NOT mutate ``os.environ`` —
    # without this field the seed would silently miss `.env`-defined values
    # on platforms whose supervisor doesn't pre-load the env file (Windows).
    projects: str | None = Field(default=None, validation_alias="PROJECTS")

    auto_scan_config: AutoScanConfig | None = Field(default=None, exclude=True)
    relay_config: RelayConfig | None = Field(default=None, exclude=True)

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        case_sensitive=False,
        # Required so ``projects=value`` (Python field name) construction
        # works alongside the ``PROJECTS`` validation_alias used by
        # pydantic-settings env loading. Without it, kwarg construction
        # would be silently ignored — matching the AutoScanConfig fix.
        populate_by_name=True,
    )

    @field_validator("audit_log_path", mode="before")
    @classmethod
    def _empty_audit_path_to_none(cls, value: Any) -> Any:
        if value == "" or value is None:
            return None
        return value

    @field_validator("log_sample_ws", mode="before")
    @classmethod
    def _parse_log_sample_ws(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in _LOG_SAMPLE_WS_TRUTHY
        return False

    @model_validator(mode="after")
    def _require_jwt_secret(self) -> "AgentConfig":
        if not self.jwt_secret_key:
            raise RuntimeError(
                "JWT_SECRET_KEY environment variable is required"
            )
        return self

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "AgentConfig":
        """Build the full agent config from environment + optional env_file.

        ``relay_config`` and ``auto_scan_config`` are loaded via their own
        ``from_env`` factories (each is a sibling ``BaseSettings`` model)
        and assigned to the result; pydantic-settings does not auto-cascade
        env_file/env-vars into nested BaseSettings without
        ``env_nested_delimiter``, which would change the env-var shape we
        ship today.
        """
        kwargs: dict[str, Any] = {}
        if env_file is not None:
            kwargs["_env_file"] = str(env_file)

        relay = RelayConfig.from_env(env_file=env_file)
        autoscan = AutoScanConfig.from_env(env_file=env_file)

        return cls(
            **kwargs,
            relay_config=relay,
            auto_scan_config=autoscan,
        )


def _read_template() -> str:
    """Return the canonical ``env.template`` packaged with the wheel."""
    return (
        files("mobaide.agent")
        .joinpath("env.template")
        .read_text(encoding="utf-8")
    )


def _resolve_prefix(prefix: str | None) -> Path:
    if prefix:
        return Path(prefix)
    from mobaide.agent.install_paths import default_prefix

    return default_prefix()


def _cmd_print_template(_args: argparse.Namespace) -> int:
    sys.stdout.write(_read_template())
    return 0


def _cmd_init_env(args: argparse.Namespace) -> int:
    from mobaide.agent._env_writer import (
        atomic_write,
        env_path_for_prefix,
        parse_env_file,
        render_template,
        restrict_perms,
    )

    prefix = _resolve_prefix(args.prefix)
    target = env_path_for_prefix(prefix)
    template = _read_template()

    if target.exists() and not args.force:
        sys.stdout.write(
            f"[mobaide-agent] {target} already exists; skipping "
            f"(pass --force to regenerate).\n"
        )
        return 0

    overrides: dict[str, str] = {
        "JWT_SECRET_KEY": secrets.token_urlsafe(32),
    }

    if target.exists() and args.force:
        existing = parse_env_file(target)
        if existing.get("JWT_SECRET_KEY"):
            overrides["JWT_SECRET_KEY"] = existing["JWT_SECRET_KEY"]
        for key in ("RELAY_AGENT_ID", "RELAY_AGENT_TOKEN"):
            if existing.get(key):
                overrides[key] = existing[key]
        if existing.get("RELAY_SERVER_URL"):
            overrides["RELAY_SERVER_URL"] = existing["RELAY_SERVER_URL"]

    rendered = render_template(template, overrides=overrides)
    atomic_write(target, rendered)
    restrict_perms(target)

    sys.stdout.write(
        f"[mobaide-agent] Wrote prepared config to {target}.\n"
        f"Next: open the mobile app, pair this agent, then run:\n"
        f"    mobaide-agent pair --id <ID> --token <TOKEN>\n"
        f"or edit the file directly:\n"
        f"    {target}\n"
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mobaide.agent.config",
        description="Agent config helpers (called by install scripts).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser(
        "init-env",
        help="Write a prepared <prefix>/mobaide_agent/.env.",
    )
    init.add_argument(
        "--prefix",
        help="Install prefix (defaults to install_paths.default_prefix()).",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if .env exists; preserves RELAY_AGENT_*.",
    )
    init.set_defaults(func=_cmd_init_env)

    pt = sub.add_parser(
        "print-template",
        help="Print the canonical env.template to stdout.",
    )
    pt.set_defaults(func=_cmd_print_template)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
