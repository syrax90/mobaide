"""Text/AST pins on the venv "bad interpreter" fix in ``deploy/install.sh``.

Sibling to ``test_install_sh.py`` (post-service pairing-QR pins) and
``test_install_ps1.py``'s ``Install-Release`` checks. Two contracts the
fix added to ``stage_release`` are pinned here, both of which are
load-bearing for the cross-platform recovery semantics introduced
alongside the updater rewrite:

1. **Completion sentinel.** ``stage_release`` must write
   ``releases/<v>/.installed`` after pip + the wheel are installed.
   ``mobaide.agent.install_paths.is_release_complete`` (and the inlined
   copies in ``mobaide.launcher``, ``deploy/windows_launcher.py``, and
   this script's own self-heal heredoc) treat a release as runnable only
   when it carries that sentinel OR the legacy ``mobaide-agent`` console
   script. A new shell-installed release has the console script, but the
   sentinel is the canonical signal — dropping its write would leave new
   installs depending solely on the legacy fallback and would silently
   diverge from the updater, which writes the sentinel as its final step.

2. **Base-interpreter hardening (R1).** ``stage_release`` must base the
   release venv off a *stable* interpreter (``resolve_base_python`` —
   ``sys._base_executable``) and clear ``VIRTUAL_ENV`` / ``PYTHONHOME``
   for the ``python -m venv`` call. The original "bad interpreter" report
   came from a venv whose interpreter symlinked into a transient venv;
   once that venv moved/vanished, ``pip`` / ``mobaide-agent`` shebangs
   pointed at a swept path. Resolving the base interpreter and dropping
   ``VIRTUAL_ENV`` makes the release venv symlink the real system
   interpreter, which never moves.

These are pure text/AST pins on the source-of-truth file — they do NOT
execute the installer. The *behaviour* of ``resolve_base_python`` (that a
venv created with a dummy ``VIRTUAL_ENV`` set still bases off the stable
interpreter) is covered by the runtime sibling
``test_install_sh_stage_release_runtime.py`` pattern; this module pins the
shape so a refactor that drops the sentinel write or reverts to a bare
``python3 -m venv`` is caught at parse time.
"""
from __future__ import annotations

import re
import subprocess
from functools import cache
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"


@cache
def _read_install_sh_text() -> str:
    assert _DEPLOY_INSTALL_SH.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_SH}"
    )
    return _DEPLOY_INSTALL_SH.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    """Extract the body extent of a bash function definition by name.

    Same canonical ``name() {{ ... }}`` extraction the sibling
    ``test_install_sh.py::_function_body`` uses: every function in
    ``deploy/install.sh`` has its closing ``}}`` at column 0, so the
    non-greedy match up to ``^}}`` slices the body without a full parser.
    """
    text = _read_install_sh_text()
    pattern = re.compile(
        rf"^{re.escape(name)}\(\)\s*\{{(.*?)^\}}",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(text)
    assert match is not None, (
        f"function {name!r} not defined in {_DEPLOY_INSTALL_SH}"
    )
    return match.group(1)


def _list_function_names() -> list[str]:
    text = _read_install_sh_text()
    return re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", text, re.M)


def test_resolve_base_python_helper_is_defined() -> None:
    """The ``resolve_base_python`` helper must exist.

    ``stage_release`` delegates the stable-interpreter resolution to it.
    If it is renamed or dropped, ``stage_release``'s ``base_python="$(...)"``
    capture calls into the void (or, under ``set -e``, aborts the install),
    and the R1 hardening that this fix added silently disappears.
    """
    funcs = _list_function_names()
    assert "resolve_base_python" in funcs, (
        f"expected function 'resolve_base_python' in {_DEPLOY_INSTALL_SH}; "
        f"found functions: {funcs!r}. stage_release bases the release venv "
        f"off the interpreter it resolves (sys._base_executable) so the "
        f"venv never symlinks a transient venv that would later leave a "
        f"'bad interpreter' shebang on pip / mobaide-agent."
    )


def test_resolve_base_python_uses_base_executable() -> None:
    """``resolve_base_python`` must resolve ``sys._base_executable``.

    The whole point of R1: when ``python3`` is itself a venv,
    ``sys.executable`` is the venv's relocatable shim but
    ``sys._base_executable`` is the real (non-venv) interpreter it was
    built from. Basing the release venv off the latter is what prevents
    the "bad interpreter" regression. A fallback to ``command -v python3``
    is acceptable (and present) when the probe yields nothing, but the
    ``_base_executable`` probe must be the primary path.
    """
    body = _function_body("resolve_base_python")
    assert "sys._base_executable" in body, (
        f"resolve_base_python must read sys._base_executable (the real "
        f"interpreter behind a venv shim); a bare sys.executable would "
        f"re-introduce the bad-interpreter bug when python3 is itself a "
        f"venv. Body:\n{body!r}"
    )


def test_stage_release_resolves_base_python_for_venv() -> None:
    """``stage_release`` must build the release venv with the resolved
    base interpreter, NOT a bare ``python3 -m venv``.

    The bug fix hinges on the venv being created from a stable
    interpreter. A regression that reverts to ``python3 -m venv
    "$release_dir/venv"`` (the pre-fix shape, still present at the
    bootstrap-verification step) would re-link the release venv to
    whatever transient ``python3`` was active and re-open the
    "bad interpreter" failure on the next prefix move.
    """
    body = _function_body("stage_release")
    assert "resolve_base_python" in body, (
        f"stage_release must call resolve_base_python to obtain the stable "
        f"interpreter for the release venv; not found in body:\n{body!r}"
    )
    # The venv-creation line must use the resolved base interpreter
    # variable, not a literal `python3 -m venv "$release_dir/venv"`.
    assert re.search(r'-m venv "\$release_dir/venv"', body), (
        f"stage_release must create the release venv at "
        f"$release_dir/venv via `-m venv`; not found in body:\n{body!r}"
    )
    assert not re.search(r'python3 -m venv "\$release_dir/venv"', body), (
        f"stage_release must NOT create the RELEASE venv with a bare "
        f"`python3 -m venv \"$release_dir/venv\"` — that re-links the venv "
        f"to a possibly-transient python3 and re-opens the bad-interpreter "
        f"bug. It must use the resolve_base_python result. Body:\n{body!r}"
    )


def test_stage_release_clears_virtualenv_for_venv_creation() -> None:
    """``stage_release`` must clear ``VIRTUAL_ENV`` (and ``PYTHONHOME``)
    for the ``-m venv`` call.

    Even with the base interpreter resolved, an inherited ``VIRTUAL_ENV``
    pointing at a transient venv can make ``python -m venv`` derive the
    new venv's ``home`` / ``pyvenv.cfg`` from that active venv rather than
    the stable base. Unsetting it for exactly the venv-creation step (via
    ``env -u VIRTUAL_ENV -u PYTHONHOME``) is the second half of the R1
    hardening; without it the fix is incomplete on hosts that run the
    installer from inside an activated venv.
    """
    body = _function_body("stage_release")
    assert "VIRTUAL_ENV" in body, (
        f"stage_release must clear VIRTUAL_ENV for the release-venv "
        f"creation (R1 hardening); not found in body:\n{body!r}"
    )
    # Pin the actual mechanism: `env -u VIRTUAL_ENV ...` scoping the unset
    # to the single venv-creation command (not a global `unset` that would
    # also strip it from the surrounding install context).
    assert re.search(r"env -u VIRTUAL_ENV", body), (
        f"stage_release must scope the VIRTUAL_ENV unset to the venv "
        f"command via `env -u VIRTUAL_ENV` so it bases off the stable "
        f"interpreter without disturbing the rest of the install "
        f"environment. Body:\n{body!r}"
    )


def test_stage_release_writes_installed_sentinel() -> None:
    """``stage_release`` must write ``releases/<v>/.installed``.

    The completion sentinel is the canonical "this release is runnable"
    signal that ``is_release_complete`` (and its inlined copies in the
    launcher, the Windows supervisor, and this script's own self-heal
    heredoc) gate on. A shell install must write it so a new release is
    recognised the same way the updater's releases are — without it, new
    installs would rely solely on the legacy console-script fallback and
    drift from the updater contract.
    """
    body = _function_body("stage_release")
    assert ".installed" in body, (
        f"stage_release must write the .installed completion sentinel into "
        f"the release dir; not found in body:\n{body!r}"
    )
    # Pin that the sentinel lands inside the release dir, not the prefix
    # root (a stray `$prefix/.installed` would mark every release complete
    # and defeat the per-release gate).
    assert re.search(r'"\$release_dir/\.installed"', body), (
        f"stage_release must write the sentinel to "
        f"\"$release_dir/.installed\" (per-release, not prefix-global); "
        f"not found in body:\n{body!r}"
    )


def test_stage_release_moves_existing_release_aside_before_build() -> None:
    """A same-version reinstall must MOVE the live release aside, not delete it.

    Two contracts in one move:

    * **Crash-safety.** ``main()`` only restarts the service after
      ``stage_release`` returns, so the prior release must survive a download /
      checksum / extraction / venv / pip failure. Deleting it up front (the old
      ``rm -rf "$release_dir"``) left a single-release install pointing at a
      missing/half-built dir with no complete sibling for the self-heal wrapper
      to recover to. Moving it to ``$release_backup`` lets ``cleanup_on_exit``
      restore it on a mid-stage abort.
    * **No stale completeness signal.** Building into a FRESH ``mkdir -p`` dir
      (the live one moved away) still guarantees neither a stale ``.installed``
      nor the prior ``venv/bin/mobaide-agent`` console script — which
      ``python -m venv`` leaves behind and ``is_release_complete``'s legacy
      fallback accepts — survives into the rebuild window. A crash before the new
      sentinel then reads as incomplete.
    """
    body = _function_body("stage_release")
    # The live release is moved aside, NOT deleted in place.
    move_at = body.find('mv "$release_dir" "$release_backup"')
    assert move_at != -1, (
        f"stage_release must move an existing same-version release aside via "
        f"`mv \"$release_dir\" \"$release_backup\"` (preserving it for restore) "
        f"rather than deleting the live dir before staging; not found in "
        f"body:\n{body!r}"
    )
    # It must NOT unconditionally destroy the live release before staging — that
    # is exactly the brick this change closes.
    assert 'rm -rf "$release_dir"' not in body, (
        f"stage_release must NOT `rm -rf \"$release_dir\"` the live release "
        f"before staging succeeds (a download/extract/pip failure would then "
        f"brick a single-release install). Move it aside instead. Body:\n{body!r}"
    )
    # The backup uses the updater's hidden '.old-*' scratch convention so every
    # release selector skips it (dotfile + non-version name) and the agent's
    # _sweep_scratch_dirs reclaims it if leaked.
    assert re.search(r'release_backup="[^"]*/releases/\.old-', body), (
        f"the move-aside backup must use the hidden '.old-<v>-<pid>' scratch "
        f"name (matching the updater's _swap_into_place) so release selectors "
        f"skip it; not found in body:\n{body!r}"
    )
    # Fresh dir + move-aside must precede the venv build and the sentinel write.
    venv_at = body.find('-m venv "$release_dir/venv"')
    write_at = body.find('> "$release_dir/.installed"')
    assert venv_at != -1 and move_at < venv_at, (
        f"the move-aside (offset {move_at}) must precede the venv build (offset "
        f"{venv_at}) so the rebuild lands in a fresh dir. Body:\n{body!r}"
    )
    assert write_at != -1 and move_at < write_at


def test_stage_release_drops_backup_on_success() -> None:
    """On success ``stage_release`` must drop the moved-aside backup.

    Once ``current_version`` points at the freshly built release the saved copy
    is dead weight AND it must be cleared so ``cleanup_on_exit`` (which keys off a
    non-empty ``$release_backup``) does not later restore it over the new release
    on an unrelated non-zero exit. The drop must come AFTER the ``current_version``
    flip and must clear the variable.
    """
    body = _function_body("stage_release")
    flip_at = body.find('mv "${MOBAIDE_AGENT_PREFIX}/current_version.tmp"')
    drop_at = body.find('rm -rf "$release_backup"', flip_at if flip_at != -1 else 0)
    assert flip_at != -1 and drop_at != -1 and flip_at < drop_at, (
        f"stage_release must drop $release_backup AFTER the current_version flip "
        f"(flip at {flip_at}, drop at {drop_at}); otherwise the EXIT trap could "
        f"restore the old release over the new one. Body:\n{body!r}"
    )
    # The drop must also clear the variable so the trap's restore is disarmed.
    assert re.search(r'rm -rf "\$release_backup"\s*\n\s*release_backup=""', body), (
        f"the success-path drop must clear release_backup (release_backup=\"\") "
        f"to disarm cleanup_on_exit's restore; not found in body:\n{body!r}"
    )


def test_cleanup_on_exit_restores_moved_aside_release() -> None:
    """The EXIT trap must restore a moved-aside release on a mid-stage abort.

    ``stage_release`` runs under ``set -e``; if any staging step fails after the
    live release was moved to ``$release_backup`` but before the success-path
    drop, the centralized ``cleanup_on_exit`` trap must put it back so the install
    is not left bricked. The restore is gated on a non-empty ``$release_backup``
    (cleared on success) so it is a no-op on the happy path.
    """
    body = _function_body("cleanup_on_exit")
    # The restore: drop the half-built dir, move the backup back into place.
    assert re.search(r'mv "\$release_backup" "\$release_dir"', body), (
        f"cleanup_on_exit must restore via `mv \"$release_backup\" "
        f"\"$release_dir\"` on a mid-stage abort; not found in body:\n{body!r}"
    )
    # Guarded so it only fires when a backup actually exists (happy-path no-op).
    assert re.search(r'-n "\$\{release_backup:-\}"', body), (
        f"cleanup_on_exit's restore must be guarded on a non-empty "
        f"${{release_backup:-}} so it is a no-op when stage_release succeeded "
        f"(which clears it) or never moved a dir aside; not found in "
        f"body:\n{body!r}"
    )


def test_stage_release_writes_sentinel_after_wheel_install() -> None:
    """The ``.installed`` write must come AFTER the wheel ``pip install``.

    Ordering is the entire contract: the sentinel means "pip AND the wheel
    are installed". If a regression moves the sentinel write above the
    ``pip install "$wheel_path"`` line, a crash between the two would leave
    a sentinel on a release with no wheel — exactly the half-built state
    the sentinel exists to exclude. The selectors would then happily run a
    release whose ``mobaide-agent`` entry point does not exist.
    """
    body = _function_body("stage_release")
    wheel_install_at = body.find('pip" install --no-cache-dir "$wheel_path"')
    # Target the sentinel WRITE (the redirect into the release dir).
    sentinel_at = body.find('> "$release_dir/.installed"')
    assert wheel_install_at != -1, (
        f"stage_release must `pip install \"$wheel_path\"` (the wheel "
        f"install step the sentinel attests to); not found in "
        f"body:\n{body!r}"
    )
    assert sentinel_at != -1, (
        f"stage_release must write the .installed sentinel; not found in "
        f"body:\n{body!r}"
    )
    assert wheel_install_at < sentinel_at, (
        f"stage_release must write .installed AFTER `pip install "
        f"\"$wheel_path\"` (found wheel install at offset {wheel_install_at}, "
        f"sentinel write at offset {sentinel_at}). Writing the sentinel "
        f"first would let a crash between the two leave a 'complete' marker "
        f"on a release with no wheel — the exact half-built state the "
        f"sentinel exists to exclude. Body:\n{body!r}"
    )


def test_stage_release_writes_sentinel_before_current_version_flip() -> None:
    """The ``.installed`` write must precede the ``current_version`` flip.

    ``current_version`` is what the launcher/supervisor read to pick the
    active release; flipping it makes the release live. The sentinel must
    be on disk before that flip so there is never a window where
    ``current_version`` points at a release that selectors would judge
    incomplete. The plan pins this as "current_version flips only after
    the sentinel".
    """
    body = _function_body("stage_release")
    # Target the sentinel WRITE (the redirect into the release dir).
    sentinel_at = body.find('> "$release_dir/.installed"')
    # The flip is the atomic rename of current_version.tmp -> current_version.
    flip_at = body.find('mv "${MOBAIDE_AGENT_PREFIX}/current_version.tmp"')
    assert flip_at != -1, (
        f"stage_release must atomically flip current_version via a "
        f"current_version.tmp -> current_version rename; not found in "
        f"body:\n{body!r}"
    )
    assert sentinel_at != -1 and sentinel_at < flip_at, (
        f"stage_release must write .installed BEFORE flipping "
        f"current_version (found sentinel at offset {sentinel_at}, flip at "
        f"offset {flip_at}). Otherwise current_version briefly points at a "
        f"release the selectors would treat as incomplete. Body:\n{body!r}"
    )


def test_self_heal_heredoc_gates_on_release_completeness() -> None:
    """The bundled self-heal recovery logic must skip incomplete releases.

    The installer ships a recovery wrapper (the heredoc near the foot of
    ``install.sh``) that rolls forward to a sibling release when the
    current one is unrunnable. After this fix it must also reject a release
    that is swapped-into-place-but-not-finalized: the trigger and the
    candidate loop both gate on ``.installed`` OR the ``mobaide-agent``
    console script. Pinned as raw text (not a function body) because the
    predicate lives in the top-level heredoc, not inside a named bash
    function.
    """
    text = _read_install_sh_text()
    # The inlined completeness predicate: sentinel OR legacy console script.
    assert re.search(r'\.installed.*\|\|.*venv/bin/mobaide-agent', text, re.S) or (
        ".installed" in text and "venv/bin/mobaide-agent" in text
    ), (
        f"install.sh's self-heal recovery must gate release selection on "
        f"completeness (\"$d/.installed\" OR \"$d/venv/bin/mobaide-agent\") "
        f"so a half-built post-swap release is never chosen. Neither the "
        f"sentinel nor the console-script fallback was found in "
        f"{_DEPLOY_INSTALL_SH}."
    )


def test_install_sh_parses_under_bash_n() -> None:
    """``deploy/install.sh`` must parse cleanly under ``bash -n``.

    Cheap insurance against syntax breakage introduced by the new helper
    and the heredoc edits. Mirrors the same ``_BASH`` lookup the sibling
    install-sh tests use so Windows hosts with Git Bash run it too;
    auto-skips where bash is unavailable.
    """
    from test_install_script_trap import _BASH

    if _BASH is None:
        pytest.skip("bash unavailable on this platform")
    proc = subprocess.run(
        [_BASH, "-n", str(_DEPLOY_INSTALL_SH)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"bash -n failed for {_DEPLOY_INSTALL_SH}: {proc.stderr!r}"
    )
