"""Helpers for rendering, parsing, and atomically writing the agent ``.env``.

Used by the install-time ``init-env`` verb (`mobaide.agent.config`) and the
end-user ``mobaide-agent pair`` verb (`mobaide.__main__`). Both flows need
the same template-walk + atomic-write semantics; keeping them here avoids
divergent line-handling logic between the two entry points.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_KEY_LINE = re.compile(r"^(?P<lead>\s*#?\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<rest>.*)$")
_FORBIDDEN_VALUE_CHARS = frozenset({"\n", "\r", "\x00"})

# Suppress the ``icacls`` console window the Scheduled-Task-spawned agent
# (no parent console under ``pythonw.exe``) would otherwise pop into the
# user's session on every env-file write. ``CREATE_NO_WINDOW`` leaves
# stdio inheritance intact so ``capture_output=True`` still works.
_WINDOWS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def render_template(
    template_text: str,
    *,
    overrides: dict[str, str] | None = None,
) -> str:
    """Substitute ``KEY=value`` lines in the template.

    For each line that parses as ``KEY=...`` (commented or not), if ``KEY``
    appears in ``overrides``, emit ``KEY=<value>`` (uncommented). Every other
    line — comments, blanks, structural prose — passes through unchanged.

    Keys present in ``overrides`` that never appear in the template are
    appended at the end. This lets callers carry user-modified values
    forward across a ``--force`` regeneration without losing them.

    Raises:
        ValueError: any override value contains ``\\r``, ``\\n``, or ``\\x00``.
            Without this guard, an attacker who controls a value (e.g. a
            ``--token`` arg whose source is the central server) can smuggle
            arbitrary additional ``KEY=VALUE`` lines into the rendered file
            and override security-critical settings such as
            ``JWT_SECRET_KEY``.
    """
    overrides = overrides or {}
    for key, value in overrides.items():
        if any(ch in _FORBIDDEN_VALUE_CHARS for ch in value):
            raise ValueError(
                f"Override value for {key!r} contains a forbidden control "
                "character (CR/LF/NUL); refusing to write a corrupt .env "
                "(would smuggle additional KEY=VALUE lines).",
            )
    seen: set[str] = set()
    out_lines: list[str] = []
    for line in template_text.splitlines():
        m = _KEY_LINE.match(line)
        if m is None:
            out_lines.append(line)
            continue
        key = m.group("key")
        if key in overrides:
            out_lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            out_lines.append(line)

    extras = [k for k in overrides if k not in seen]
    if extras:
        if out_lines and out_lines[-1] != "":
            out_lines.append("")
        for key in extras:
            out_lines.append(f"{key}={overrides[key]}")

    rendered = "\n".join(out_lines)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def parse_env_file(source: Path | str) -> dict[str, str]:
    """Return ``KEY -> value`` for an existing ``.env``-style file or text.

    Accepts either a ``Path`` (read from disk) or a pre-read ``str`` (parse
    directly — saves a syscall and closes the TOCTOU window when the caller
    has already loaded the file content).

    Comment lines (``#``) and blank lines are skipped. Quotes around the
    value are stripped if they wrap the entire value. Unparseable lines are
    silently dropped — this is a best-effort reader for carrying user-set
    values through ``--force``, not a full dotenv replacement.
    """
    if isinstance(source, Path):
        if not source.exists():
            return {}
        text = source.read_text(encoding="utf-8")
    else:
        text = source
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.lstrip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def atomic_write(path: Path, content: str, *, mode: int | None = None) -> None:
    """Write ``content`` to ``path`` atomically.

    Writes to ``<path>.tmp`` first (in the same directory so the rename is
    a single inode operation on POSIX), fsyncs the file, then ``os.replace``s
    onto the target. The parent directory is created if missing. ``mode``,
    when given, is applied to the new file before the rename (the temp file
    is created ``0o600``).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def restrict_perms(path: Path) -> None:
    """Tighten ``.env`` permissions; never raises.

    POSIX: ``chmod 0o600`` (owner-only read/write).
    Windows: best-effort ``icacls`` call grants ``Administrators:F`` /
    ``${USERNAME}:F`` and strips inheritance. If the call fails, the file
    keeps its inherited NTFS ACL — log and continue.
    """
    if not path.exists():
        return
    if sys.platform == "win32":
        _restrict_perms_windows(path)
    else:
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            logger.warning("env_writer.chmod_failed path=%s: %s", path, exc)


def _restrict_perms_windows(path: Path) -> None:
    """Best-effort Windows ACL hardening.

    ``/inheritance:r`` strips broad-readers like ``Users``. We then re-grant
    the principals that legitimately need to access the file:

    * ``Administrators:F`` — the install scripts run elevated and may need
      to re-run ``init-env --force`` to rotate credentials.
    * ``${USERNAME}:F`` — the interactive user who ran the installer; the
      Scheduled Task ``MobaideAgent`` runs in this user's session and
      ``pairing_monitor.write_credentials_to_env`` atomic-replaces the
      file at pair time, which needs ``DELETE`` on the target and
      ``WRITE_DAC`` for the follow-up icacls re-tighten — both included
      in ``F``. The historic ``SYSTEM:F`` grant was for a LocalSystem
      SCM service host; that host is gone (the agent now runs in the
      user's session — see ``deploy/install.ps1:Install-Service``), so
      the SYSTEM grant was dropped with it.

    Failures are logged at DEBUG and never raised.
    """
    user = os.environ.get("USERNAME") or ""
    grants: list[str] = [
        "/grant", "Administrators:F",
    ]
    if user:
        grants += ["/grant", f"{user}:F"]
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", *grants],
            check=False,
            capture_output=True,
            timeout=10,
            creationflags=_WINDOWS_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(
            "env_writer.icacls_failed path=%s: %s", path, exc,
        )


def blank_relay_credentials(env_file: Path, *, clear_agent_id: bool) -> None:
    """Blank ``RELAY_AGENT_TOKEN`` (and, with ``clear_agent_id``,
    ``RELAY_AGENT_ID``) in ``env_file``, keeping every other line."""
    overrides = {"RELAY_AGENT_TOKEN": ""}
    if clear_agent_id:
        overrides["RELAY_AGENT_ID"] = ""
    rendered = render_template(
        env_file.read_text(encoding="utf-8"), overrides=overrides
    )
    atomic_write(env_file, rendered)
    restrict_perms(env_file)


def env_path_for_prefix(prefix: Path) -> Path:
    """Canonical path of the agent ``.env`` under an install prefix."""
    return prefix / "mobaide_agent" / ".env"
