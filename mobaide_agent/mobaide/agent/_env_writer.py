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


def atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Writes to ``<path>.tmp`` first (in the same directory so the rename is
    a single inode operation on POSIX), fsyncs the file, then ``os.replace``s
    onto the target. The parent directory is created if missing.
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
    Windows: best-effort ``icacls`` call grants ``SYSTEM:F`` /
    ``Administrators:F`` / ``${USERNAME}:F`` and strips inheritance. The
    LocalSystem-hosted service needs full control (not just read) because
    ``pairing_monitor.write_credentials_to_env`` runs inside that service
    and atomic-replaces the file at pair time. If the call fails, the file
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

    * ``SYSTEM:F`` — the Windows service runs as LocalSystem
      (``deploy/install.ps1`` registers ``MobaideAgent`` without an ``obj=``
      override) AND ``pairing_monitor.write_credentials_to_env`` writes
      ``RELAY_AGENT_ID`` / ``RELAY_AGENT_TOKEN`` into this same file at
      runtime. ``atomic_write``'s ``os.replace(tmp, target)`` is
      ``MoveFileExW(..., MOVEFILE_REPLACE_EXISTING)`` which needs ``DELETE``
      on the target; the follow-up ``restrict_perms`` after the rewrite
      needs ``WRITE_DAC`` to re-tighten the ACL (without it the rewritten
      file inherits Program Files' default ``Users:Read``, leaking the
      newly-written ``RELAY_AGENT_TOKEN`` to every local account). A
      previous design granted ``SYSTEM:R`` which satisfied the boot-time
      read but silently failed the post-pairing write — the pairing-monitor
      task raised ``PermissionError`` inside ``os.replace`` and the agent
      stayed in ``pairing_pending`` forever. Granting ``F`` does not expand
      the attack surface: ``NT AUTHORITY\\SYSTEM`` already has unconstrained
      access to every file on the machine via the kernel object manager.
    * ``Administrators:F`` — the install scripts run elevated and may need
      to re-run ``init-env --force`` to rotate credentials.
    * ``${USERNAME}:F`` — the interactive user who ran the installer.

    Failures are logged at DEBUG and never raised.
    """
    user = os.environ.get("USERNAME") or ""
    grants: list[str] = [
        "/grant", "SYSTEM:F",
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
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(
            "env_writer.icacls_failed path=%s: %s", path, exc,
        )


def env_path_for_prefix(prefix: Path) -> Path:
    """Canonical path of the agent ``.env`` under an install prefix."""
    return prefix / "mobaide_agent" / ".env"
