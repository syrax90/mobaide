"""Resolve the current release dir and exec into its venv's Python."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_prefix() -> Path:
    explicit = os.environ.get("CLAUDE_AGENT_PREFIX")
    if explicit:
        return Path(explicit)
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        return Path(program_files) / "ClaudeAgent"
    if sys.platform == "darwin":
        return Path("/usr/local/claude-mobile")
    return Path("/opt/claude-mobile")


def _resolve_python(release_dir: Path) -> Path:
    if sys.platform == "win32":
        return release_dir / "venv" / "Scripts" / "python.exe"
    return release_dir / "venv" / "bin" / "python"


def main() -> None:
    prefix = _default_prefix()
    current_version_file = prefix / "current_version"
    try:
        version = current_version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        sys.stderr.write(
            f"claude-agent-launcher: cannot read {current_version_file}: {exc}\n"
        )
        sys.exit(70)

    if not version:
        sys.stderr.write(
            f"claude-agent-launcher: {current_version_file} is empty\n"
        )
        sys.exit(70)

    release_dir = prefix / "releases" / version
    python = _resolve_python(release_dir)
    if not python.exists():
        sys.stderr.write(
            f"claude-agent-launcher: missing interpreter {python}\n"
        )
        sys.exit(70)

    args = [str(python), "-m", "claude_mobile", "run", *sys.argv[1:]]
    os.execv(str(python), args)


if __name__ == "__main__":
    main()
