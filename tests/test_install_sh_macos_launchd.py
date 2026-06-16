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


def test_generated_launcher_uses_absolute_bin_bash_shebang() -> None:
    """The generated launcher heredoc must start with ``#!/bin/bash``.

    THE reported root cause. With ``#!/usr/bin/env bash``, launchd execs
    ``/usr/bin/env`` which searches the plist ``PATH`` for ``bash`` — and
    macOS keeps bash only at ``/bin/bash``, which the pre-fix ``PATH``
    omitted — so it failed with ``env: bash: No such file or directory``,
    the launcher never exec'd, and ``/api/health`` never came up. An
    absolute interpreter path removes the PATH dependency entirely.
    """
    body = _sh_function_body("install_launchd_plist")
    assert "#!/bin/bash" in body, (
        "install_launchd_plist must emit the launcher with an absolute "
        "`#!/bin/bash` shebang — macOS ships bash only at /bin/bash. "
        "Body:\n" + repr(body)
    )
    assert "#!/usr/bin/env bash" not in body, (
        "the generated launcher must NOT use `#!/usr/bin/env bash`: "
        "launchd's plist PATH omits /bin, so `env` cannot resolve bash "
        "and fails with `env: bash: No such file or directory` "
        "(the exact failure this fix removes). Body:\n" + repr(body)
    )


def test_generated_plist_path_includes_bin_and_sbin() -> None:
    """The generated plist ``PATH`` must include ``/bin`` and ``/sbin``.

    The launcher's ``#!/bin/bash`` and other core tools live only under
    ``/bin``/``/sbin`` on macOS; launchd's minimal default PATH omits them.
    Without ``/bin`` on PATH the original ``env bash`` lookup failed.
    """
    body = _sh_function_body("install_launchd_plist")
    path_lines = [ln for ln in body.splitlines() if "<key>PATH</key>" in ln]
    assert path_lines, (
        "install_launchd_plist must emit a PATH key in the plist "
        "EnvironmentVariables. Body:\n" + repr(body)
    )
    path_line = path_lines[0]
    segments = re.split(r"[:>]", path_line)
    assert "/bin" in segments, (
        "generated plist PATH must contain /bin as its own entry "
        "(macOS keeps bash at /bin/bash; the launcher shebang is "
        f"#!/bin/bash). PATH line:\n  {path_line!r}"
    )
    assert "/sbin" in segments, (
        "generated plist PATH must contain /sbin as its own entry. "
        f"PATH line:\n  {path_line!r}"
    )


def test_generated_plist_path_uses_expanded_run_home_not_escaped_home() -> None:
    """The generated plist ``PATH`` must substitute the expanded
    ``$RUN_HOME`` (resolved at install time), NOT an escaped ``\\$HOME``.

    launchd does not expand ``$VAR`` in ``EnvironmentVariables``, so a
    ``\\$HOME/.npm-global/bin`` entry would land as the literal string
    ``$HOME/.npm-global/bin`` and never resolve. The heredoc therefore
    must let the shell substitute ``$RUN_HOME`` (unescaped) before the
    file is written.
    """
    body = _sh_function_body("install_launchd_plist")
    path_lines = [ln for ln in body.splitlines() if "<key>PATH</key>" in ln]
    assert path_lines, (
        "install_launchd_plist must emit a PATH key. Body:\n" + repr(body)
    )
    path_line = path_lines[0]
    assert "$RUN_HOME/.npm-global/bin" in path_line, (
        "generated plist PATH must use the install-time-expanded "
        "$RUN_HOME for the npm-global entry. PATH line:\n  "
        + repr(path_line)
    )
    assert "$RUN_HOME/.local/bin" in path_line, (
        "generated plist PATH must use the install-time-expanded "
        "$RUN_HOME for the .local/bin entry. PATH line:\n  "
        + repr(path_line)
    )
    assert "\\$HOME" not in path_line, (
        "generated plist PATH must NOT contain an escaped \\$HOME — "
        "launchd does not expand $VAR, so it would land as the literal "
        "string `$HOME/...` and never resolve. The heredoc must "
        "substitute the expanded $RUN_HOME instead. PATH line:\n  "
        + repr(path_line)
    )


def test_generated_plist_sets_home_to_run_home() -> None:
    """The generated plist must export ``HOME`` = ``$RUN_HOME`` in
    ``EnvironmentVariables``.

    launchd does not inherit the caller's ``HOME``; the launcher and the
    Claude Code CLI it shells out to need ``$HOME`` pointed at the target
    user's real home (config, caches, the venv's certifi bundle path).
    """
    body = _sh_function_body("install_launchd_plist")
    assert re.search(
        r"<key>HOME</key>\s*<string>\$RUN_HOME</string>", body
    ), (
        "install_launchd_plist must emit `<key>HOME</key><string>"
        "$RUN_HOME</string>` so the agent runs with the target user's "
        "real home (launchd does not inherit HOME). Body:\n" + repr(body)
    )


def test_generated_plist_sets_throttle_interval() -> None:
    """The generated plist must set ``ThrottleInterval`` (parity with the
    static reference template and the rollback heuristic).

    Without throttling, ``KeepAlive`` re-execs the launcher instantly on
    crash, hiding a degraded boot loop from the 3-strike rollback window.
    """
    body = _sh_function_body("install_launchd_plist")
    assert "<key>ThrottleInterval</key>" in body, (
        "install_launchd_plist must emit a ThrottleInterval key so a "
        "crash-looping launcher does not re-exec instantly (parity with "
        "deploy/launchd/com.mobaide.agent.plist). Body:\n" + repr(body)
    )


def test_install_launchd_plist_loads_via_bootstrap_gui_domain() -> None:
    """``install_launchd_plist`` must load the LaunchAgent via
    ``launchctl bootstrap gui/<uid>`` (modern, user-domain), preceded by a
    ``bootout`` for idempotency.

    A root ``launchctl load`` of a per-user LaunchAgent warns ("Got
    LaunchAgents instead") and runs the agent in the wrong domain as root.
    ``bootstrap gui/$RUN_UID`` loads it in the user's Aqua session; the
    prior ``bootout`` makes re-install reload cleanly.
    """
    body = _sh_function_body("install_launchd_plist")
    assert "gui/" in body, (
        "install_launchd_plist must load the agent in the user's GUI "
        "domain (gui/<uid>), not a root `launchctl load`. Body:\n"
        + repr(body)
    )
    # Anchor on the actual launchctl invocations, not the prose mention
    # of "bootstrap" in the function's leading comment.
    bootout_pos = body.find("launchctl_user bootout")
    bootstrap_pos = body.find("launchctl_user bootstrap")
    assert bootstrap_pos >= 0, (
        "install_launchd_plist must invoke `launchctl_user bootstrap "
        "<domain> <plist>` to load the agent in the GUI domain. Body:\n"
        + repr(body)
    )
    assert bootout_pos >= 0, (
        "install_launchd_plist must invoke `launchctl_user bootout` "
        "before `bootstrap` so a re-install reloads idempotently. "
        "Body:\n" + repr(body)
    )
    assert bootout_pos < bootstrap_pos, (
        f"`launchctl_user bootout` (pos {bootout_pos}) must precede "
        f"`launchctl_user bootstrap` (pos {bootstrap_pos}) so the stale "
        f"job is torn down before the new one loads. Body:\n" + repr(body)
    )


def test_install_launchd_plist_falls_back_to_legacy_load() -> None:
    """``install_launchd_plist`` must keep a legacy ``launchctl load``
    fallback for when ``bootstrap`` fails (no GUI session, e.g. headless /
    SSH).

    ``bootstrap gui/<uid>`` needs an active Aqua session. On a headless
    host it fails; the installer must degrade to the legacy load rather
    than hard-regress vs the previous behaviour.
    """
    body = _sh_function_body("install_launchd_plist")
    assert re.search(r"launchctl(_user)?\s+load", body), (
        "install_launchd_plist must retain a `launchctl load` fallback "
        "for hosts with no GUI session where `bootstrap` fails. Body:\n"
        + repr(body)
    )
    # Anchor on the launchctl call, not the comment's "bootstrap" prose.
    bootstrap_pos = body.find("launchctl_user bootstrap")
    load_pos = body.rfind("load")
    assert bootstrap_pos >= 0 and load_pos > bootstrap_pos, (
        "the legacy `launchctl load` fallback must come AFTER the "
        f"`launchctl_user bootstrap` attempt (bootstrap pos "
        f"{bootstrap_pos}, last load pos {load_pos}) — it is the "
        f"no-GUI-session degradation path. Body:\n" + repr(body)
    )


def test_install_launchd_plist_chowns_prefix_to_run_user_when_root() -> None:
    """When running as root, ``install_launchd_plist`` must ``chown`` the
    install prefix to ``$RUN_USER`` so the user-domain agent can write
    logs / run / projects.

    macOS auto-elevates (the prefix is ``/usr/local/mobaide``), but the
    agent runs as the GUI user. Mirrors the Linux chown in
    ``install_systemd_unit``; without it the user-domain launcher cannot
    write into a root-owned prefix.
    """
    body = _sh_function_body("install_launchd_plist")
    assert "chown" in body, (
        "install_launchd_plist must chown the prefix to the run user "
        "(it auto-elevates to root but the agent runs as the GUI user). "
        "Body:\n" + repr(body)
    )
    assert "$RUN_USER" in body and "$RUN_GROUP" in body, (
        "the chown must target $RUN_USER:$RUN_GROUP (the resolved GUI "
        "user), matching the Linux install_systemd_unit chown. Body:\n"
        + repr(body)
    )


def test_resolve_run_as_user_macos_is_defined() -> None:
    """``resolve_run_as_user_macos`` must exist.

    ``main()``'s darwin branch calls it before ``install_launchd_plist``
    to populate ``RUN_USER`` / ``RUN_UID`` / ``RUN_HOME``; if it is
    renamed or dropped, the plist generation reads unset variables.
    """
    funcs = _list_function_names()
    assert "resolve_run_as_user_macos" in funcs, (
        f"expected function 'resolve_run_as_user_macos' in "
        f"{_DEPLOY_INSTALL_SH}; found: {funcs!r}. install_launchd_plist "
        f"depends on the RUN_USER/RUN_UID/RUN_HOME it sets."
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


def test_launchctl_user_helper_is_defined() -> None:
    """The ``launchctl_user`` helper must exist.

    ``install_launchd_plist`` routes every launchctl call (bootout /
    bootstrap / enable / kickstart) through it so they target the user's
    GUI session from a root context.
    """
    funcs = _list_function_names()
    assert "launchctl_user" in funcs, (
        f"expected function 'launchctl_user' in {_DEPLOY_INSTALL_SH}; "
        f"found: {funcs!r}. install_launchd_plist dispatches launchctl "
        f"through it."
    )


def test_launchctl_user_uses_asuser_when_root() -> None:
    """``launchctl_user`` must run via ``launchctl asuser "$RUN_UID"`` when
    root, and call launchctl directly otherwise.

    After auto-elevation the installer is root; ``launchctl asuser <uid>``
    attaches to the user's Aqua session so ``gui/<uid>`` resolves and the
    loaded job runs as that user. When already the user (a user-writable
    prefix install), a direct call is correct and ``asuser`` is
    unavailable.
    """
    body = _sh_function_body("launchctl_user")
    assert re.search(r"id -u", body), (
        "launchctl_user must branch on whether it is running as root "
        "(`id -u` == 0). Body:\n" + repr(body)
    )
    assert re.search(r'launchctl\s+asuser\s+"\$RUN_UID"', body), (
        "launchctl_user must dispatch via `launchctl asuser "
        "\"$RUN_UID\"` in the root branch so gui/<uid> resolves and the "
        "job runs as the target user. Body:\n" + repr(body)
    )


def test_main_resolves_run_user_before_installing_launchd_plist() -> None:
    """``main()``'s darwin branch must call ``resolve_run_as_user_macos``
    before ``install_launchd_plist``.

    ``install_launchd_plist`` reads ``RUN_USER`` / ``RUN_UID`` /
    ``RUN_HOME`` / ``RUN_GROUP`` that the resolver sets. Reversing the
    order would generate the plist against unset variables (empty
    gui/<uid> domain, empty HOME). Mirrors the Linux
    ``resolve_run_as_user`` → ``install_systemd_unit`` ordering.
    """
    body = _sh_function_body("main")
    resolve_pos = body.find("resolve_run_as_user_macos")
    install_pos = body.find("install_launchd_plist")
    assert resolve_pos >= 0, (
        "main's darwin branch must call resolve_run_as_user_macos. "
        "Body:\n" + repr(body)
    )
    assert install_pos >= 0, (
        "main's darwin branch must call install_launchd_plist. Body:\n"
        + repr(body)
    )
    assert resolve_pos < install_pos, (
        f"main must call resolve_run_as_user_macos (pos {resolve_pos}) "
        f"BEFORE install_launchd_plist (pos {install_pos}); the latter "
        f"reads RUN_USER/RUN_UID/RUN_HOME the resolver sets. Body:\n"
        + repr(body)
    )


def test_wait_agent_healthy_macos_hint_uses_launchctl_print_gui() -> None:
    """``wait_agent_healthy``'s macOS failure hint must use ``launchctl
    print gui/<uid>/com.mobaide.agent``.

    The agent now lives in the user's GUI domain, so the old
    ``launchctl list | grep`` (run by root) would not see it. The hint
    must point the operator at the gui-domain print so they can diagnose
    a service that failed to start.
    """
    body = _sh_function_body("wait_agent_healthy")
    assert "launchctl print gui/" in body, (
        "wait_agent_healthy's macOS diagnostic must use `launchctl print "
        "gui/<uid>/com.mobaide.agent` — the gui-domain agent is invisible "
        "to the old root `launchctl list | grep`. Body:\n" + repr(body)
    )
    assert "launchctl list | grep com.mobaide.agent" not in body, (
        "wait_agent_healthy must NOT keep the stale `launchctl list | "
        "grep com.mobaide.agent` hint — a root `launchctl list` cannot "
        "see the user's gui-domain agent. Body:\n" + repr(body)
    )


def test_reference_plist_path_includes_bin_and_sbin() -> None:
    """The static reference template ``deploy/launchd/com.mobaide.agent.plist``
    must include ``/bin`` and ``/sbin`` in its ``PATH``.

    Keeps the documented hand-install template consistent with what
    install.sh generates; a manual installer using the old template would
    hit the same ``env: bash`` failure.
    """
    text = _read_plist_text()
    # The reference template puts <key>PATH</key> and its <string> value on
    # separate lines (unlike the single-line generated plist), so match the
    # value across the newline.
    m = re.search(
        r"<key>PATH</key>\s*<string>([^<]*)</string>", text, re.DOTALL
    )
    assert m is not None, (
        f"reference plist {_DEPLOY_PLIST} must declare a PATH key with a "
        f"<string> value."
    )
    path_value = m.group(1)
    segments = path_value.split(":")
    assert "/bin" in segments and "/sbin" in segments, (
        "reference plist PATH must contain /bin and /sbin (macOS keeps "
        "bash at /bin/bash). PATH value:\n  " + repr(path_value)
    )


def test_reference_plist_declares_home_key() -> None:
    """The static reference template must declare a ``HOME`` key.

    launchd does not inherit ``HOME``; the template documents the key (and
    notes the manual-install caveat that ``$HOME`` must be substituted with
    an absolute path) so a hand-install matches the generated plist.
    """
    text = _read_plist_text()
    assert "<key>HOME</key>" in text, (
        f"reference plist {_DEPLOY_PLIST} must declare a HOME key in "
        f"EnvironmentVariables (launchd does not inherit HOME)."
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


def test_install_launchd_plist_warns_headless_needs_gui_login() -> None:
    """[CORR-02] ``install_launchd_plist``'s bootstrap-failure ``warn``
    must explain that a per-user LaunchAgent needs an active GUI desktop
    login and that the health check will time out on a headless host.

    ``launchctl bootstrap gui/<uid>`` requires an active Aqua session; on
    a headless / SSH-only Mac it fails and the installer degrades to the
    legacy load. The expanded warning is the operator's only signal for
    why ``/api/health`` then times out, so it must name the GUI-login
    requirement rather than failing silently. (Pinned because this module
    already pins operator-facing diagnostic strings — see
    ``test_wait_agent_healthy_macos_hint_uses_launchctl_print_gui``.)
    """
    body = _sh_function_body("install_launchd_plist")
    warn_lines = [ln for ln in body.splitlines() if "warn " in ln]
    assert warn_lines, (
        "install_launchd_plist must warn when bootstrap into the GUI "
        "domain fails (the headless-fallback path). Body:\n" + repr(body)
    )
    lowered = body.lower()
    assert "gui" in lowered and "login" in lowered, (
        "the headless-fallback warn must explain that a per-user "
        "LaunchAgent needs an active GUI (desktop) login session. "
        "Body:\n" + repr(body)
    )
    assert "headless" in lowered, (
        "the headless-fallback warn must name the headless / SSH-only "
        "case so the operator can map the symptom to their environment. "
        "Body:\n" + repr(body)
    )
    assert "time out" in lowered or "timeout" in lowered or "time-out" in lowered, (
        "the headless-fallback warn must tell the operator the health "
        "check below will time out (the observable symptom of a "
        "LaunchAgent that could not start without a GUI session). "
        "Body:\n" + repr(body)
    )
