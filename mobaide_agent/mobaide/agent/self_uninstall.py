"""Remove an installed agent from its host when the relay orders it.

The relay sends ``agent_uninstall`` after the owner removed this agent from
their account with the uninstall option. Removal runs in three steps:

1. Disarm: blank the relay credentials in ``.env``. Whatever happens next,
   the agent can no longer reconnect.
2. Unregister the supervisor and delete the install. Each OS does this
   differently:

   * Linux: the agent runs unprivileged under a root-owned systemd unit, so
     it cannot remove the unit itself. It drops a marker that
     ``deploy/install.sh``'s root helper unit watches. Installs older than
     that helper get a partial fallback: an ``exit 0`` launcher stub that
     ``Restart=on-failure`` leaves alone, plus deletion of everything else.
   * macOS: the user owns the LaunchAgent plist and the prefix. The agent
     removes both, then calls ``launchctl bootout``, which kills it.
   * Windows: the running venv is locked, and the launcher's kill-on-close
     Job Object would take a child process down with it. So the delete
     runs from a one-shot Scheduled Task, outside that job, which retries
     until the locks are gone. The task runs elevated, so its script lives
     in the admin-only prefix and runs under ``python -I``: from the user's
     %TEMP% any unelevated process could swap it or plant a module beside
     it.
3. Exit 0, which no supervisor restarts.

Voice input is stopped first and its model and runtime (~700 MB) deleted by
the agent itself, before any of the above OS steps: root helpers written by
an older ``install.sh`` do not list the voice dirs, and on Windows a running
STT helper would hold its DLLs and model files open.

Only the known install entries are deleted, never the whole prefix:
``MOBAIDE_AGENT_PREFIX`` can point at a directory that holds other files.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from mobaide.agent._env_writer import blank_relay_credentials, env_path_for_prefix
from mobaide.agent.pairing_qr import clear_pending_pairing
from mobaide.agent.updater import InstallLayout
from mobaide.voice.store import VoicePaths, remove_tree

if TYPE_CHECKING:
    from mobaide.voice.service import VoiceService

logger = logging.getLogger(__name__)

# The voice model and runtime (~700 MB); ``voice-fake`` holds a test install.
VOICE_PREFIX_ENTRIES: tuple[str, ...] = ("voice", "voice-fake")

# Everything the installers, the launchers and the agent create directly
# under the prefix. The root helper unit written by deploy/install.sh
# repeats this list; keep the two in sync.
PREFIX_ENTRIES: tuple[str, ...] = (
    "releases",
    "trust",
    "mobaide_agent",
    "run",
    "state",
    "logs",
    "svc",
    "bin",
    "current_version",
    "current_version.tmp",
    ".boot_journal",
    "agent.env",
    "agent.out.log",
    "agent.err.log",
    "users.json",
    "projects.json",
    *VOICE_PREFIX_ENTRIES,
)

UNINSTALL_MARKER = Path("run") / "uninstall-requested"
SYSTEMD_HELPER_PATH_UNIT = Path("/etc/systemd/system/mobaide-agent-uninstall.path")
LINUX_LAUNCHER = Path("bin") / "mobaide-agent-launcher.sh"
LAUNCHD_LABEL = "com.mobaide.agent"
WINDOWS_TASK_NAME = "MobaideAgent"
WINDOWS_CLEANUP_TASK_NAME = "MobaideAgentCleanup"
# Deliberately absent from PREFIX_ENTRIES: the script deletes itself last.
WINDOWS_CLEANUP_SCRIPT_NAME = "uninstall_cleanup.py"

_SUBPROCESS_TIMEOUT_S = 30
_EXIT_DELAY_S = 0.5
_WINDOWS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_LINUX_LAUNCHER_STUB = """#!/bin/sh
# The Mobaide agent was removed from its account and uninstalled itself.
# This stub only keeps the leftover systemd unit from crash-looping; remove
# both with:
#   sudo systemctl disable mobaide-agent
#   sudo rm -f /etc/systemd/system/mobaide-agent.service
#   sudo rm -rf {prefix}
exit 0
"""

# Run by the one-shot cleanup task's own interpreter (the base Python, never
# the release venv being deleted). Stdlib only; the placeholders are
# replaced with repr() literals.
_WINDOWS_CLEANUP_SCRIPT = """import os, shutil, subprocess, time
prefix = __PREFIX__
entries = __ENTRIES__
for _ in range(120):
    for name in entries:
        path = os.path.join(prefix, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass
    if not any(os.path.lexists(os.path.join(prefix, n)) for n in entries):
        break
    time.sleep(5)
subprocess.run(
    ["schtasks.exe", "/Delete", "/TN", __TASK__, "/F"],
    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
)
try:
    os.remove(__file__)
except OSError:
    pass
try:
    os.rmdir(prefix)
except OSError:
    pass
"""


async def handle_uninstall_request(
    prefix: Path,
    *,
    installed: bool,
    voice: VoiceService | None = None,
    exit_func: Callable[[int], object] = sys.exit,
) -> None:
    """Uninstall the agent at ``prefix`` and schedule a clean exit.

    ``installed`` is false for a developer / source run, which shares the
    default prefix with a real install: such a run deletes and rewrites
    nothing, so a test relay can never take out the installed agent.
    """
    if not installed:
        logger.warning(
            "agent.uninstall.skipped_not_installed prefix=%s — the relay "
            "removed this agent; it stays offline until it is paired again",
            prefix,
        )
        return
    logger.warning("agent.uninstall.start prefix=%s", prefix)
    voice_root: Path | None = None
    if voice is not None:
        voice_root = voice.paths.root
        await voice.shutdown()
    try:
        await asyncio.to_thread(uninstall, prefix, voice_root=voice_root)
    except Exception:
        logger.exception("agent.uninstall.failed prefix=%s", prefix)
    asyncio.get_running_loop().call_later(_EXIT_DELAY_S, exit_func, 0)


def uninstall(
    prefix: Path,
    *,
    platform: str = sys.platform,
    voice_root: Path | None = None,
) -> None:
    """Disarm, unregister and delete the install at ``prefix``. Blocking.

    ``voice_root`` is the running agent's voice dir, which
    ``MOBAIDE_VOICE_DIR`` can move away from ``<prefix>/voice``.

    A failed disarm must not stop the removal: deleting the install takes the
    credentials with it, and on macOS a skipped bootout would leave the
    ``KeepAlive`` job relaunching the agent into the same order every 10 s.
    """
    try:
        disarm(prefix)
    except Exception:
        logger.exception("agent.uninstall.disarm_failed prefix=%s", prefix)
    try:
        remove_voice_files(prefix, voice_root)
    except Exception:
        logger.exception("agent.uninstall.voice_remove_failed prefix=%s", prefix)
    if platform.startswith("linux"):
        _uninstall_linux(prefix)
    elif platform == "darwin":
        _uninstall_macos(prefix)
    elif platform == "win32":
        _uninstall_windows(prefix)
    else:
        remove_prefix_entries(prefix)


def disarm(prefix: Path) -> None:
    """Blank the relay credentials and drop any pending pairing."""
    env_file = env_path_for_prefix(prefix)
    if env_file.exists():
        blank_relay_credentials(env_file, clear_agent_id=True)
    clear_pending_pairing(InstallLayout(prefix=prefix))
    logger.info("agent.uninstall.disarmed env=%s", env_file)


def remove_voice_files(prefix: Path, voice_root: Path | None = None) -> None:
    """Delete the voice dirs under ``prefix``. Where the files live elsewhere
    (``voice_root``, or the target of a symlinked ``<prefix>/voice``), delete
    only what the voice layout names there, as ``mobaide-agent voice remove``
    does, plus the settings and lock; the link itself goes with the other
    prefix entries."""
    roots: list[Path] = []
    for name in VOICE_PREFIX_ENTRIES:
        path = prefix / name
        if path.is_symlink():
            roots.append(path)
        else:
            remove_tree(path)
    if voice_root is not None and voice_root not in [prefix / n for n in VOICE_PREFIX_ENTRIES]:
        roots.append(voice_root)
    for root in roots:
        paths = VoicePaths(root)
        remove_tree(paths.models_dir)
        remove_tree(paths.runtime_root)
        for path in (paths.settings_file, paths.lock_file):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("agent.uninstall.remove_failed path=%s: %s", path, exc)
        if not root.is_symlink():
            try:
                root.rmdir()
            except OSError:
                pass


def remove_prefix_entries(prefix: Path, *, keep: frozenset[str] = frozenset()) -> None:
    """Delete the known install entries under ``prefix``, then the prefix
    itself when that left it empty. Failures are logged, never raised."""
    for name in PREFIX_ENTRIES:
        if name in keep:
            continue
        path = prefix / name
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("agent.uninstall.remove_failed path=%s: %s", path, exc)
    if not keep:
        try:
            prefix.rmdir()
        except OSError:
            pass


def _uninstall_linux(prefix: Path) -> None:
    if SYSTEMD_HELPER_PATH_UNIT.exists():
        marker = prefix / UNINSTALL_MARKER
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            logger.info("agent.uninstall.helper_triggered marker=%s", marker)
            return
        except OSError as exc:
            logger.warning("agent.uninstall.marker_failed path=%s: %s", marker, exc)
    launcher = prefix / LINUX_LAUNCHER
    try:
        if launcher.exists():
            launcher.write_text(
                _LINUX_LAUNCHER_STUB.format(prefix=prefix), encoding="utf-8"
            )
            launcher.chmod(0o755)
    except OSError as exc:
        logger.warning("agent.uninstall.stub_failed path=%s: %s", launcher, exc)
    remove_prefix_entries(prefix, keep=frozenset({"bin"}))
    logger.warning(
        "agent.uninstall.partial — this install predates the uninstall "
        "helper, so the systemd unit is left in place as a no-op. Finish "
        "with: sudo systemctl disable mobaide-agent && sudo rm -f "
        "/etc/systemd/system/mobaide-agent.service && sudo rm -rf %s",
        prefix,
    )


def _uninstall_macos(prefix: Path) -> None:
    import pwd

    uid = os.getuid()
    try:
        plist = (
            Path(pwd.getpwuid(uid).pw_dir)
            / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        )
        plist.unlink(missing_ok=True)
    except (KeyError, OSError) as exc:
        logger.warning("agent.uninstall.plist_remove_failed: %s", exc)
    try:
        remove_prefix_entries(prefix)
    except Exception:
        logger.exception("agent.uninstall.remove_failed prefix=%s", prefix)
    logger.info("agent.uninstall.bootout label=%s", LAUNCHD_LABEL)
    # Last step: bootout stops this very process. ``remove`` covers a job the
    # installer's legacy fallback loaded outside the gui/<uid> domain.
    if _run(["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"]) != 0:
        _run(["launchctl", "remove", LAUNCHD_LABEL])


def _uninstall_windows(prefix: Path) -> None:
    python = _base_pythonw(prefix)
    if python is None:
        logger.warning(
            "agent.uninstall.no_cleanup_interpreter — files under %s stay; "
            "delete the folder by hand",
            prefix,
        )
    else:
        script = prefix / WINDOWS_CLEANUP_SCRIPT_NAME
        try:
            script.write_text(
                _WINDOWS_CLEANUP_SCRIPT
                .replace("__PREFIX__", repr(str(prefix)))
                .replace("__ENTRIES__", repr(PREFIX_ENTRIES))
                .replace("__TASK__", repr(WINDOWS_CLEANUP_TASK_NAME)),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("agent.uninstall.cleanup_script_failed path=%s: %s", script, exc)
            python = None
    if python is not None:
        rc = _run([
            "schtasks.exe", "/Create", "/TN", WINDOWS_CLEANUP_TASK_NAME,
            "/TR", f'"{python}" -I "{script}"',
            "/SC", "ONCE", "/ST", "00:00", "/RL", "HIGHEST", "/F",
        ])
        if rc == 0:
            rc = _run(["schtasks.exe", "/Run", "/TN", WINDOWS_CLEANUP_TASK_NAME])
        if rc != 0:
            logger.warning(
                "agent.uninstall.cleanup_task_failed rc=%s — files under %s stay",
                rc, prefix,
            )
    _run(["schtasks.exe", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"])


def _base_pythonw(prefix: Path) -> Path | None:
    """The base interpreter's ``pythonw.exe``, provided it lives outside the
    prefix about to be deleted."""
    base = Path(getattr(sys, "_base_executable", "") or sys.executable)
    for name in ("pythonw.exe", "python.exe"):
        candidate = base.with_name(name)
        try:
            if candidate.is_file() and not candidate.resolve().is_relative_to(
                prefix.resolve()
            ):
                return candidate
        except OSError:
            continue
    return None


def _run(cmd: list[str]) -> int:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_SUBPROCESS_TIMEOUT_S,
            creationflags=_WINDOWS_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("agent.uninstall.command_failed cmd=%s: %s", cmd[0], exc)
        return -1
    if result.returncode != 0:
        logger.warning(
            "agent.uninstall.command_failed cmd=%s rc=%s stderr=%s",
            cmd[0], result.returncode, result.stderr.strip(),
        )
    return result.returncode
