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

    # Deployment environment selector (MOBAIDE_ENV). "production" (the default)
    # keeps every other field and subsystem at its production behaviour. "test"
    # opts a disposable instance into the fake PTY backend (no real shells),
    # plaintext ws:// relay, and temp-dir state isolation. Mirrors the
    # central_server selector; needs validation_alias because
    # env_prefix="" would otherwise bind a bare ``env`` field to ``ENV``.
    env: str = Field(default="production", validation_alias="MOBAIDE_ENV")

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

    # Operator override pointing at the absolute path of the Claude Code CLI
    # executable (e.g. ``C:\\Users\\<u>\\AppData\\Roaming\\npm\\claude.cmd`` on
    # Windows). Read here for the same reason as ``projects`` above:
    # pydantic-settings populates this instance from .env but does NOT mutate
    # ``os.environ``, so without a typed field the resolver in
    # ``claude_launcher.py`` would never see a ``MOBAIDE_CLAUDE_BIN`` line in
    # .env (``extra="ignore"`` drops it on the floor). ``install.ps1``
    # detects the user's ``claude.cmd`` at install time (under the
    # originating user's env, before UAC elevation) and writes its
    # absolute path here so the agent always resolves it regardless of
    # PATH layout in its runtime session.
    claude_bin: str | None = Field(
        default=None, validation_alias="MOBAIDE_CLAUDE_BIN",
    )

    # Operator override pointing at the absolute path of the Codex CLI
    # executable (``codex.cmd`` on Windows, ``codex`` on POSIX). Mirrors
    # ``claude_bin`` and needs its own typed field for the same reason —
    # pydantic-settings populates this from .env without mutating
    # ``os.environ``, so the resolver in ``codex_launcher.py`` would never see
    # a ``MOBAIDE_CODEX_BIN`` line in .env (``extra="ignore"`` drops it).
    codex_bin: str | None = Field(
        default=None, validation_alias="MOBAIDE_CODEX_BIN",
    )

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

    @field_validator(
        "user_store_path", "projects_store_path", "audit_log_path", mode="after"
    )
    @classmethod
    def _anchor_relative_path(cls, value: str | None) -> str | None:
        """Anchor a relative store/log path to the writable state prefix.

        The defaults (and any relative override) otherwise resolve against the
        process CWD. launchd/systemd pin CWD to the prefix today, but a launch
        with CWD=``/`` (a read-only volume on modern macOS) crashes the first
        store write with ``[Errno 30] Read-only file system: projects.json.tmp``.
        Anchoring makes the path CWD-independent.

        ``state_prefix()`` resolves to the install prefix for an installed
        agent (unchanged) but falls back to a per-user data dir when that
        prefix isn't writable — the source/dev run case, where the OS-wide
        default (``C:\\Program Files\\MobaideAgent`` / ``/opt/mobaide``) is
        unwritable by an unprivileged process and the first store write would
        otherwise die with ``[Errno 13] Permission denied``.

        Explicit absolute paths — and ``audit_log_path=None`` — pass through.
        """
        if value is None:
            return None
        if Path(value).is_absolute():
            return value
        from mobaide.agent.install_paths import state_prefix

        return str(state_prefix() / value)

    @model_validator(mode="after")
    def _require_jwt_secret(self) -> "AgentConfig":
        if not self.jwt_secret_key:
            raise RuntimeError(
                "JWT_SECRET_KEY environment variable is required"
            )
        return self

    @property
    def is_test_mode(self) -> bool:
        """True when MOBAIDE_ENV=test selects the disposable test profile.

        Exact-string match (mirrors the central_server selector): only the
        literal ``test`` opts in — ``TEST``/``staging``/``""`` stay production.
        """
        return self.env == "test"

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
        if existing.get("MOBAIDE_CLAUDE_BIN"):
            overrides["MOBAIDE_CLAUDE_BIN"] = existing["MOBAIDE_CLAUDE_BIN"]
        if existing.get("MOBAIDE_CODEX_BIN"):
            overrides["MOBAIDE_CODEX_BIN"] = existing["MOBAIDE_CODEX_BIN"]

    # An explicit --claude-bin / --codex-bin from the installer wins over a
    # preserved existing value: a re-install where the operator just moved the
    # claude/codex package to a new prefix needs the new path to land.
    if args.claude_bin:
        overrides["MOBAIDE_CLAUDE_BIN"] = args.claude_bin
    if args.codex_bin:
        overrides["MOBAIDE_CODEX_BIN"] = args.codex_bin

    rendered = render_template(template, overrides=overrides)
    atomic_write(target, rendered)
    restrict_perms(target)

    sys.stdout.write(
        f"[mobaide-agent] Wrote prepared config to {target}.\n"
        f"Next: render the pairing QR and scan it from the Mobaide mobile app:\n"
        f"    mobaide-agent pair-qr --print     # render once and exit\n"
        f"    mobaide-agent pair-qr --wait      # render and block until claimed\n"
        f"or edit the file directly:\n"
        f"    {target}\n"
    )
    return 0


def _cmd_set_env(args: argparse.Namespace) -> int:
    """Set a single ``KEY=value`` in an existing ``.env`` in-place.

    Used by ``install.ps1`` to surgically backfill ``MOBAIDE_CLAUDE_BIN``
    / ``MOBAIDE_CODEX_BIN`` on a re-install over an already-paired agent
    without touching ``JWT_SECRET_KEY`` / ``RELAY_AGENT_TOKEN``.
    ``init-env --force`` preserves those keys explicitly, but it's
    heavier — it rewrites every templated line. ``set-env`` is the
    smallest operation that achieves the same outcome for one key.
    """
    from mobaide.agent._env_writer import (
        atomic_write,
        env_path_for_prefix,
        render_template,
        restrict_perms,
    )

    prefix = _resolve_prefix(args.prefix)
    target = env_path_for_prefix(prefix)
    if not target.exists():
        sys.stderr.write(
            f"[mobaide-agent] {target} does not exist; run init-env first.\n"
        )
        return 1

    existing_text = target.read_text(encoding="utf-8")
    rendered = render_template(existing_text, overrides={args.key: args.value})
    atomic_write(target, rendered)
    restrict_perms(target)
    sys.stdout.write(f"[mobaide-agent] Set {args.key} in {target}.\n")
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
    init.add_argument(
        "--claude-bin",
        dest="claude_bin",
        help=(
            "Absolute path to claude.cmd / claude executable; written as "
            "MOBAIDE_CLAUDE_BIN. Used by install.ps1 on Windows."
        ),
    )
    init.add_argument(
        "--codex-bin",
        dest="codex_bin",
        help=(
            "Absolute path to codex.cmd / codex executable; written as "
            "MOBAIDE_CODEX_BIN. Used by install.ps1 on Windows."
        ),
    )
    init.set_defaults(func=_cmd_init_env)

    set_env = sub.add_parser(
        "set-env",
        help=(
            "Set a single KEY=value in an existing .env (in-place; preserves "
            "all other keys). Used by install.ps1 to backfill "
            "MOBAIDE_CLAUDE_BIN / MOBAIDE_CODEX_BIN."
        ),
    )
    set_env.add_argument(
        "--prefix",
        help="Install prefix (defaults to install_paths.default_prefix()).",
    )
    set_env.add_argument(
        "--key", required=True, help="The KEY to set in .env.",
    )
    set_env.add_argument(
        "--value", required=True, help="The value to write for --key.",
    )
    set_env.set_defaults(func=_cmd_set_env)

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
