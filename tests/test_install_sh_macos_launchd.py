"""Pins on the macOS launchd fix in ``deploy/install.sh`` (+ the static
``deploy/launchd/com.mobaide.agent.plist`` reference template).

The reported failure: ``http://127.0.0.1:8000/api/health did not respond
within 60s``. Root cause — the generated launcher used
``#!/usr/bin/env bash`` while the launchd plist ``PATH`` omitted ``/bin``.
macOS keeps bash only at ``/bin/bash``, so ``env`` could not resolve
``bash`` and the launcher never exec'd (``env: bash: No such file or
directory``, repeated by ``KeepAlive``). uvicorn never bound 8000 → health
timed out. Additionally launchd does NOT expand ``$HOME`` in
``EnvironmentVariables``, so the home-relative ``PATH`` entries were literal
``$HOME/...`` garbage, and a root ``launchctl load`` of a per-user
LaunchAgent warned and ran the agent as root.

The fix: absolute ``#!/bin/bash`` shebang; plist ``PATH`` gains ``/bin:/sbin``
and substitutes the expanded ``$RUN_HOME`` at install time; a ``HOME`` key;
``resolve_run_as_user_macos`` resolves the GUI user; ``launchctl_user`` runs
launchctl in that user's session; ``install_launchd_plist`` loads via
``launchctl bootstrap gui/<uid>`` with bootout/enable/kickstart and a legacy
``load`` fallback. The Linux systemd path is untouched.

A second macOS-only defect in the same install path: the plist set no
``WorkingDirectory``, so the agent ran with CWD ``/`` (read-only system
volume on modern macOS). Its ``projects.json`` / ``users.json`` store paths
are relative (``AgentConfig`` defaults) and are designed to resolve under
the supervisor's working directory — the systemd unit pins
``WorkingDirectory=$MOBAIDE_AGENT_PREFIX`` but the launchd plist did not, so
the first-boot store write crashed with ``OSError: [Errno 30] Read-only file
system: 'projects.json.tmp'`` and 8000 never bound (``KeepAlive`` looped it).
The plist now pins ``WorkingDirectory`` to the writable prefix, matching
systemd.

These tests are pure text/AST pins on the source-of-truth files (the same
convention as ``test_install_sh.py`` / ``test_install_sh_heredoc_no_subst.py``);
they do NOT execute the installer. End-to-end execution is covered by the
manual verification steps in the plan and by CI distro images. The shared
``bash -n`` parse net is already provided by
``test_install_sh.py::test_install_sh_parses_under_bash_n`` and is not
duplicated here.
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from test_install_script_trap import _sh_function_body


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"
_DEPLOY_PLIST = _REPO_ROOT / "deploy" / "launchd" / "com.mobaide.agent.plist"


@cache
def _read_install_sh_text() -> str:
    assert _DEPLOY_INSTALL_SH.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_SH}"
    )
    return _DEPLOY_INSTALL_SH.read_text(encoding="utf-8")


@cache
def _read_plist_text() -> str:
    assert _DEPLOY_PLIST.is_file(), (
        f"missing launchd reference plist: {_DEPLOY_PLIST}"
    )
    return _DEPLOY_PLIST.read_text(encoding="utf-8")


def _list_function_names() -> list[str]:
    return re.findall(
        r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", _read_install_sh_text(), re.M
    )


def test_resolve_run_as_user_macos_resolution_precedence() -> None:
    """``resolve_run_as_user_macos`` must resolve the run user in the
    order: ``MOBAIDE_RUN_AS_USER`` → ``SUDO_USER`` → non-root self →
    console owner (``stat -f '%Su' /dev/console``).

    The explicit override wins first; under sudo the invoking user is the
    GUI user; a non-root install runs as itself; only a bare root shell
    falls back to whoever owns the desktop console. Reordering these
    (e.g. console owner before SUDO_USER) would pick the wrong session.
    """
    body = _sh_function_body("resolve_run_as_user_macos")
    env_pos = body.find("MOBAIDE_RUN_AS_USER")
    sudo_pos = body.find("SUDO_USER")
    console_pos = body.find("/dev/console")
    assert env_pos >= 0, (
        "resolve_run_as_user_macos must honour the MOBAIDE_RUN_AS_USER "
        "override. Body:\n" + repr(body)
    )
    assert sudo_pos >= 0, (
        "resolve_run_as_user_macos must consider SUDO_USER (the invoking "
        "user after auto-elevation). Body:\n" + repr(body)
    )
    assert console_pos >= 0, (
        "resolve_run_as_user_macos must fall back to the console owner "
        "via `stat -f '%Su' /dev/console` for a bare root shell. Body:\n"
        + repr(body)
    )
    assert env_pos < sudo_pos < console_pos, (
        f"resolution precedence is wrong: expected MOBAIDE_RUN_AS_USER "
        f"(pos {env_pos}) < SUDO_USER (pos {sudo_pos}) < /dev/console "
        f"console owner (pos {console_pos}). The explicit override must "
        f"win, then the sudo invoker, and only a bare root shell may "
        f"fall back to the desktop console owner. Body:\n" + repr(body)
    )


def test_resolve_run_as_user_macos_refuses_root() -> None:
    """``resolve_run_as_user_macos`` must hard-fail if the resolved user is
    ``root``.

    A dev agent that manages the user's source tree must never run as
    root. The function must call ``fail`` when ``RUN_USER`` resolves to
    ``root`` rather than proceeding to load a root-owned agent.
    """
    body = _sh_function_body("resolve_run_as_user_macos")
    assert re.search(r'\[\s*"\$RUN_USER"\s*=\s*"root"\s*\]', body), (
        "resolve_run_as_user_macos must test `[ \"$RUN_USER\" = "
        "\"root\" ]` and reject it. Body:\n" + repr(body)
    )
    assert "fail" in body, (
        "resolve_run_as_user_macos must call `fail` when the resolved "
        "user is root — the agent must not run as root. Body:\n"
        + repr(body)
    )


def test_resolve_run_as_user_macos_rejects_unsafe_username_charset() -> None:
    """``resolve_run_as_user_macos`` must reject any ``RUN_USER`` outside
    the macOS short-name charset ``[A-Za-z0-9._-]`` (and the empty string).

    [SEC-01] ``RUN_USER`` is sourced from ``MOBAIDE_RUN_AS_USER`` /
    ``SUDO_USER`` / the ``/dev/console`` owner and then flows into ``id``,
    ``dscl``, ``chown``, the tilde ``eval echo ~$RUN_USER``, and the
    generated plist. A value containing a shell metacharacter (``;``,
    ``$()``, a quote, whitespace) would be an injection sink at those
    sites. The guard is a ``case "$RUN_USER" in`` with an
    ``''|*[!A-Za-z0-9._-]*)`` arm that calls ``fail`` before any of those
    consumers run. A regression that drops or loosens it re-opens the
    injection vector.
    """
    body = _sh_function_body("resolve_run_as_user_macos")
    assert re.search(r'case\s+"\$RUN_USER"\s+in', body), (
        "resolve_run_as_user_macos must guard $RUN_USER with a "
        '`case "$RUN_USER" in` statement before it reaches id/dscl/chown/'
        "eval/plist. Body:\n" + repr(body)
    )
    assert "[!A-Za-z0-9._-]" in body, (
        "resolve_run_as_user_macos must reject usernames outside the "
        "macOS short-name charset via a `*[!A-Za-z0-9._-]*` case pattern "
        "(rejecting any shell metacharacter before it flows into id/dscl/"
        "chown/eval/plist). Body:\n" + repr(body)
    )
    assert re.search(r"''\s*\|\s*\*\[!A-Za-z0-9\._-\]\*\)", body), (
        "the charset guard must also reject the empty username — the "
        "case arm must be `''|*[!A-Za-z0-9._-]*)` so an unset console "
        "owner (empty `stat -f '%Su' /dev/console`) is rejected, not "
        "passed downstream. Body:\n" + repr(body)
    )


def test_resolve_run_as_user_macos_charset_guard_runs_before_id_check() -> None:
    """The [SEC-01] charset guard must sit AFTER the root-refusal check and
    BEFORE the ``id "$RUN_USER"`` existence probe.

    Ordering is the whole point of the guard: ``id "$RUN_USER"`` is the
    first consumer that interpolates the unvalidated name, so the
    ``case``/charset rejection must run first. (It is placed after the
    root-refusal so the more specific "never root" diagnostic still wins
    for the literal ``root``.) If the guard were reordered below the
    ``id`` probe, a metacharacter username would already have reached a
    command substitution before being rejected.
    """
    body = _sh_function_body("resolve_run_as_user_macos")
    root_pos = body.find('"$RUN_USER" = "root"')
    guard_pos = body.find("[!A-Za-z0-9._-]")
    id_check_pos = body.find('id "$RUN_USER"')
    assert root_pos >= 0, (
        "resolve_run_as_user_macos must keep the root-refusal check. "
        "Body:\n" + repr(body)
    )
    assert guard_pos >= 0, (
        "resolve_run_as_user_macos must contain the charset guard "
        "pattern `[!A-Za-z0-9._-]`. Body:\n" + repr(body)
    )
    assert id_check_pos >= 0, (
        "resolve_run_as_user_macos must probe `id \"$RUN_USER\"` for "
        "existence. Body:\n" + repr(body)
    )
    assert root_pos < guard_pos < id_check_pos, (
        f"the [SEC-01] charset guard (pos {guard_pos}) must sit AFTER the "
        f"root-refusal check (pos {root_pos}) and BEFORE the "
        f"`id \"$RUN_USER\"` existence probe (pos {id_check_pos}) — id is "
        f"the first consumer that interpolates the unvalidated username, "
        f"so the rejection must run first. Body:\n" + repr(body)
    )

