"""Pinned installs of the npm-packaged ACP adapters.

Each adapter version is installed once into the agent's state folder and then
started with ``node`` directly. Not through ``npx``: ``npm exec`` exports its
own settings (``npm_config_ignore_scripts``, ``npm_config_omit`` …) into the
environment of the program it runs, so every command the coding agent ran
would inherit them, and an ``npm install`` it ran in the user's project would
silently skip install scripts.

Optional dependencies are omitted: they are the adapters' bundled copies of
the coding agent's own binary (hundreds of MB), and a chat session always
drives the host's CLI instead.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mobaide.core.cli_launcher import WINDOWS_NO_WINDOW, shim_safe_argv
from mobaide.core.git_exec import _kill_tree

logger = logging.getLogger(__name__)

INSTALL_TIMEOUT = 600.0
INSTALLED_MARKER = ".installed"
STDERR_HINT_MAX = 300
STAGING_PREFIX = ".staging-"
# A staging folder this old was left by an install that never finished.
STALE_STAGING_SECONDS = 2 * INSTALL_TIMEOUT

# Installs and other blocking adapter work run here, not in the default
# executor, where every running terminal session holds a blocked read.
INSTALL_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="acp-install")
# Short blocking checks, kept apart so a long install never delays them.
PROBE_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="acp-probe")


class AdapterInstallError(RuntimeError):
    """npm could not install the adapter; the text goes to the user."""


@dataclass(frozen=True)
class NpmAdapter:
    package: str
    version: str
    # The command of the package, also the name of a global install.
    bin_name: str
    # npm ``--before``: dependency ranges resolve to versions published by
    # then, so a later (possibly malicious) release is never picked up.
    resolve_before: str | None = None

    @property
    def spec(self) -> str:
        return f"{self.package}@{self.version}"

    @property
    def folder_name(self) -> str:
        return f"{self.package.rsplit('/', 1)[-1]}-{self.version}"


def _bin_entry(package_dir: Path, bin_name: str) -> Path | None:
    try:
        meta = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    bins = meta.get("bin") if isinstance(meta, dict) else None
    rel = bins.get(bin_name) if isinstance(bins, dict) else bins
    if not isinstance(rel, str) or not rel:
        return None
    entry = (package_dir / rel).resolve()
    # A bin path must stay inside its own package.
    if package_dir.resolve() not in entry.parents or not entry.is_file():
        return None
    return entry


class AdapterInstaller:
    """One pinned adapter under ``root``; safe to share between threads."""

    def __init__(self, adapter: NpmAdapter, root: Path) -> None:
        self.adapter = adapter
        self.root = root
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self.root / self.adapter.folder_name

    def _package_dir(self, base: Path) -> Path:
        return base.joinpath("node_modules", *self.adapter.package.split("/"))

    def entry(self) -> Path | None:
        """The installed adapter's script, or None before a complete install."""
        if not (self.directory / INSTALLED_MARKER).is_file():
            return None
        return _bin_entry(self._package_dir(self.directory), self.adapter.bin_name)

    def install(self, npm: str, env: Mapping[str, str]) -> Path:
        """Install the adapter unless it is there already (blocking)."""
        with self._lock:
            try:
                return self._install_locked(npm, env)
            except OSError as exc:
                raise AdapterInstallError(f"Could not install {self.adapter.spec}: {exc}") from None

    def _install_locked(self, npm: str, env: Mapping[str, str]) -> Path:
        entry = self.entry()
        if entry is not None:
            return entry
        self.root.mkdir(parents=True, exist_ok=True)
        self._remove_stale_staging()
        staging = self.root / (
            f"{STAGING_PREFIX}{self.adapter.folder_name}-{os.getpid()}-{secrets.token_hex(4)}"
        )
        try:
            self._run_npm(npm, staging, env)
            if _bin_entry(self._package_dir(staging), self.adapter.bin_name) is None:
                raise AdapterInstallError(
                    f"The {self.adapter.package} package has no {self.adapter.bin_name} command"
                )
            (staging / INSTALLED_MARKER).write_text(self.adapter.version, encoding="utf-8")
            if self.directory.exists():
                shutil.rmtree(self.directory, ignore_errors=True)
            os.replace(staging, self.directory)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        self._prune_other_versions()
        entry = self.entry()
        if entry is None:
            raise AdapterInstallError(f"{self.adapter.spec} did not install completely")
        logger.info("acp.adapter_installed adapter=%s", self.adapter.spec)
        return entry

    def _run_npm(self, npm: str, staging: Path, env: Mapping[str, str]) -> None:
        flags = [
            "--ignore-scripts",
            "--omit=optional",
            "--no-audit",
            "--no-fund",
            "--no-update-notifier",
            "--loglevel=error",
        ]
        if self.adapter.resolve_before:
            flags.append(f"--before={self.adapter.resolve_before}")
        # Explicit flags win over any npm_config_* the environment carries,
        # while a registry or proxy set there still applies.
        argv = shim_safe_argv(npm, "install", "--prefix", str(staging), *flags, self.adapter.spec)
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(self.root),
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=WINDOWS_NO_WINDOW,
                start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            raise AdapterInstallError(f"Could not run npm: {exc}") from None
        try:
            _, stderr = proc.communicate(timeout=INSTALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            # npm runs under ``cmd /c`` on Windows: only a tree kill ends it.
            _kill_tree(proc)
            try:
                proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            raise AdapterInstallError(f"Downloading {self.adapter.spec} took too long") from None
        if proc.returncode != 0:
            tail = stderr.decode("utf-8", "replace").strip().splitlines()
            hint = next((line for line in reversed(tail) if line.strip()), "")
            raise AdapterInstallError(
                f"Could not download {self.adapter.spec}"
                + (f": {hint[:STDERR_HINT_MAX]}" if hint else "")
            )

    def _remove_stale_staging(self) -> None:
        # Any version: an install of an earlier version may have been cut off.
        prefix = f"{STAGING_PREFIX}{self.adapter.folder_name[: -len(self.adapter.version)]}"
        now = time.time()
        try:
            leftovers = [p for p in self.root.iterdir() if p.name.startswith(prefix)]
        except OSError:
            return
        for path in leftovers:
            try:
                if now - path.stat().st_mtime > STALE_STAGING_SECONDS:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

    def _prune_other_versions(self) -> None:
        prefix = self.adapter.folder_name[: -len(self.adapter.version)]
        try:
            siblings = list(self.root.iterdir())
        except OSError:
            return
        for path in siblings:
            name = path.name
            if path == self.directory or not name.startswith(prefix):
                continue
            # Another adapter whose name extends this one ("x-acp-2" vs "x-acp-extra-1").
            if not name[len(prefix):][:1].isdigit():
                continue
            shutil.rmtree(path, ignore_errors=True)
