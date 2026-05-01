"""Resolve the current release dir and exec into its venv's Python.

Beyond the bare exec, the launcher tracks supervisor restart frequency
in a small boot journal and rolls back to the previous good release if
the active version crashes 3 times within 60 seconds. Supervisor units
(systemd, launchd, Windows Service) keep restarting; the launcher is
the persistent entry point that observes the loop and breaks it by
rewriting ``current_version`` to a sibling release. The next supervisor
restart then boots the rolled-back version.

Self-contained on purpose — this module runs across release directories
(it's installed at ``<prefix>/bin/mobaide-agent-launcher``, not inside
any ``releases/<v>/venv``) and must not import ``mobaide`` so it
survives a partially-staged or corrupt release.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Crash-loop window. A version that is exec'd this many times within
# this many seconds is considered failing, and the launcher reverts to
# the highest-versioned sibling release (if any) on the next boot.
ROLLBACK_THRESHOLD = 3
ROLLBACK_WINDOW_SECONDS = 60.0
JOURNAL_KEEP_LINES = 100
JOURNAL_NAME = ".boot_journal"


def _default_prefix() -> Path:
    explicit = os.environ.get("MOBAIDE_AGENT_PREFIX")
    if explicit:
        return Path(explicit)
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        return Path(program_files) / "MobaideAgent"
    if sys.platform == "darwin":
        return Path("/usr/local/mobaide")
    return Path("/opt/mobaide")


def _resolve_python(release_dir: Path) -> Path:
    if sys.platform == "win32":
        return release_dir / "venv" / "Scripts" / "python.exe"
    return release_dir / "venv" / "bin" / "python"


def _read_current_version(prefix: Path) -> str | None:
    try:
        return (prefix / "current_version").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_current_version(prefix: Path, version: str) -> None:
    target = prefix / "current_version"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(version + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _append_boot_journal(prefix: Path, version: str, now: float) -> list[tuple[float, str]]:
    """Append ``now version`` to the journal and return the trimmed entries.

    Best-effort: journal failures never block boot — they only disable
    the rollback heuristic for this attempt.
    """
    journal = prefix / JOURNAL_NAME
    entries: list[tuple[float, str]] = _read_journal(journal)
    entries.append((now, version))
    if len(entries) > JOURNAL_KEEP_LINES:
        entries = entries[-JOURNAL_KEEP_LINES:]
    try:
        prefix.mkdir(parents=True, exist_ok=True)
        tmp = journal.with_suffix(journal.suffix + ".tmp")
        tmp.write_text(
            "".join(f"{ts:.3f} {v}\n" for ts, v in entries),
            encoding="utf-8",
        )
        os.replace(tmp, journal)
    except OSError:
        pass
    return entries


def _read_journal(journal: Path) -> list[tuple[float, str]]:
    try:
        text = journal.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[tuple[float, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ts_str, version = line.split(" ", 1)
            out.append((float(ts_str), version.strip()))
        except (ValueError, IndexError):
            continue
    return out


def _crash_count(entries: list[tuple[float, str]], version: str, now: float) -> int:
    cutoff = now - ROLLBACK_WINDOW_SECONDS
    return sum(1 for ts, v in entries if v == version and ts >= cutoff)


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in v.split("."))
    except ValueError:
        return ()


def _find_rollback_target(
    prefix: Path,
    current_version: str,
    journal_entries: list[tuple[float, str]] | None = None,
    now: float | None = None,
) -> str | None:
    """Pick the highest-versioned sibling release with a usable venv.

    A candidate that has itself crossed the crash-loop threshold within
    the same window is excluded — without this, a deployment where two
    consecutive releases are both broken oscillates between them until
    the supervisor's hard cap finally fires, leaving an unpredictable
    ``current_version`` on disk.

    Returns ``None`` when no candidate qualifies (fresh install, only
    the failing release on disk, or every sibling is also crash-looping).
    """
    releases_dir = prefix / "releases"
    try:
        candidates = [p for p in releases_dir.iterdir() if p.is_dir()]
    except OSError:
        return None
    eligible: list[tuple[tuple[int, ...], str]] = []
    for candidate in candidates:
        version = candidate.name
        if version == current_version:
            continue
        if not _resolve_python(candidate).exists():
            continue
        if (
            journal_entries is not None
            and now is not None
            and _crash_count(journal_entries, version, now) >= ROLLBACK_THRESHOLD
        ):
            continue
        eligible.append((_parse_version(version), version))
    if not eligible:
        return None
    eligible.sort(reverse=True)
    return eligible[0][1]


def _exec_release(prefix: Path, version: str) -> None:
    release_dir = prefix / "releases" / version
    python = _resolve_python(release_dir)
    if not python.exists():
        sys.stderr.write(
            f"mobaide-agent-launcher: missing interpreter {python}\n"
        )
        sys.exit(70)
    args = [str(python), "-m", "mobaide", "run", *sys.argv[1:]]
    os.execv(str(python), args)


def main() -> None:
    prefix = _default_prefix()
    version = _read_current_version(prefix)
    if version is None:
        sys.stderr.write(
            f"mobaide-agent-launcher: cannot read {prefix / 'current_version'}\n"
        )
        sys.exit(70)

    now = time.time()
    entries = _append_boot_journal(prefix, version, now)

    if _crash_count(entries, version, now) >= ROLLBACK_THRESHOLD:
        rollback = _find_rollback_target(prefix, version, entries, now)
        if rollback is not None:
            try:
                _write_current_version(prefix, rollback)
            except OSError as exc:
                sys.stderr.write(
                    f"mobaide-agent-launcher: rollback write failed: {exc}\n"
                )
            else:
                sys.stderr.write(
                    f"mobaide-agent-launcher: ERROR — version {version} "
                    f"crashed {ROLLBACK_THRESHOLD}+ times in "
                    f"{ROLLBACK_WINDOW_SECONDS:.0f}s; rolling back to {rollback}\n"
                )
                version = rollback

    _exec_release(prefix, version)


if __name__ == "__main__":
    main()
