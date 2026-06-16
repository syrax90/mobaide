"""Regression pin for the wheel-staging heredoc in ``deploy/install.sh``.

macOS ships bash 3.2.57 (GPLv3). Its parser has a known bug: a
here-document nested inside a ``$(...)`` command substitution is
re-scanned as live shell text, so the heredoc body's quotes and parens
are counted. ``stage_release``'s Python staging body contains an odd
number of single quotes (``'version'``, ``'unknown'``, and an
apostrophe in a comment), so the pre-fix shape

    wheel_path="$(python3 - ... <<'PY'
    ... ~50 lines of Python ...
    PY
    )"

made bash 3.2 hit EOF "looking for matching `'`" and abort the whole
install with ``line 862: syntax error: unexpected end of file`` —
before a single statement ran. bash 4/5 (Linux CI) parse the same
shape fine, which is exactly why this slipped through.

The fix redirects Python's stdout to a file under the already-managed
``$tmpdir`` and reads it back, taking the heredoc out of ``$(...)``:

    python3 - ... > "$tmpdir/wheel_path" <<'PY'
    ...
    PY
    wheel_path="$(cat "$tmpdir/wheel_path")"

These tests pin that contract. The structural/AST checks are
platform-independent (they catch the regression on bash 4/5 too, where
``bash -n`` would not), mirroring the pure-text pins in
``test_install_sh.py``. The bash-3.2 execution checks run only on the
bug's actual interpreter and are auto-skipped elsewhere.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from test_install_script_trap import _BASH, _sh_function_body


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"

# Matches a here-document *operator* at the point it opens a body:
# ``<<WORD``, ``<<-WORD``, ``<<'WORD'``, ``<<"WORD"`` (optionally quoted
# delimiter). Deliberately distinct from a bare ``<<`` so the Python
# left-shift ``fh.read(1 << 20)`` inside the staging body is NOT counted
# as a heredoc.
_HEREDOC_OPENER = re.compile(r"<<-?\s*(['\"]?)[A-Za-z_][A-Za-z0-9_]*\1")


def _bash_major_minor() -> tuple[int, int] | None:
    """Return ``(major, minor)`` for the resolved bash, or ``None``.

    The heredoc-in-``$()`` parser bug is specific to bash 3.2; on bash
    4+ the buggy shape parses cleanly, so the execution-level checks are
    only meaningful (and the negative control only fires) on 3.x.
    """
    if _BASH is None:
        return None
    proc = subprocess.run(
        [_BASH, "-c", 'printf "%s" "${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"'],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        major_s, minor_s = proc.stdout.strip().split(".", 1)
        return int(major_s), int(minor_s)
    except ValueError:
        return None


_BASH_VER = _bash_major_minor()
_IS_BASH_3X = _BASH_VER is not None and _BASH_VER[0] == 3


def _line_is_inside_unclosed_paren_subst(line: str) -> bool:
    """True if ``line`` opens more ``$(`` than it closes with ``)``.

    A heredoc opener on such a line is sitting inside a still-open
    command substitution — the exact shape bash 3.2 misparses. This is
    the line-scoped form of the plan's ``grep -nE '\\$\\(.*<<'`` guard,
    tightened to the heredoc *operator* so it cannot false-positive on
    the Python ``1 << 20`` left-shift.
    """
    return line.count("$(") > line.count(")")


def test_stage_release_stages_wheel_via_redirect_to_file() -> None:
    """``stage_release`` must run the staging heredoc with its stdout
    redirected to ``$tmpdir/wheel_path`` (NOT captured in ``$(...)``).

    This is the load-bearing half of the fix: the heredoc lives as a
    plain command, so bash 3.2 never re-scans its body. A regression
    that folds it back into ``wheel_path="$(python3 - ... <<'PY' ...)"``
    re-introduces the macOS parse failure.
    """
    body = _sh_function_body("stage_release")
    assert re.search(
        r'python3 -[^\n]*>\s*"\$tmpdir/wheel_path"[^\n]*<<', body
    ), (
        "stage_release must invoke the staging heredoc as a plain "
        "command with stdout redirected to a file "
        '(`python3 - ... > "$tmpdir/wheel_path" <<\'PY\'`), not inside a '
        "$(...) command substitution. The redirect form keeps the "
        "heredoc out of $(...) so macOS bash 3.2.57 does not re-scan "
        "the Python body and trip on its odd quote count "
        "(line 862: syntax error: unexpected end of file). Body:\n"
        + repr(body)
    )


def test_stage_release_reads_wheel_path_back_from_file() -> None:
    """``stage_release`` must read the staged wheel path back via
    ``wheel_path="$(cat "$tmpdir/wheel_path")"``.

    Pairs with the redirect above: the redirect writes the single line
    the Python ``print(wheel)`` emits; this read-back restores the same
    captured value (``$(cat ...)`` strips the trailing newline just as
    the old ``$(python3 ...)`` did). Dropping it leaves ``$wheel_path``
    empty and the subsequent ``pip install "$wheel_path"`` fails.
    """
    body = _sh_function_body("stage_release")
    assert 'wheel_path="$(cat "$tmpdir/wheel_path")"' in body, (
        "stage_release must read the staged wheel path back with "
        'wheel_path="$(cat "$tmpdir/wheel_path")" after the heredoc. '
        "This restores the exact value the old "
        'wheel_path="$(python3 - ... <<\'PY\')" captured, so '
        '`pip install --no-cache-dir "$wheel_path"` still receives the '
        "canonical PEP 427 wheel path. Body:\n" + repr(body)
    )


def test_stage_release_has_no_heredoc_inside_command_substitution() -> None:
    """No here-document opener in ``stage_release`` may sit inside an
    unclosed ``$(...)``.

    The durable, platform-independent guard: it catches the regression
    on bash 4/5 (Linux CI), where ``bash -n`` would happily parse the
    buggy shape and let the macOS-only break ship. The check targets the
    heredoc *operator* specifically, so the Python ``fh.read(1 << 20)``
    left-shift in the staging body is not mistaken for a heredoc.
    """
    body = _sh_function_body("stage_release")
    offenders = [
        line
        for line in body.splitlines()
        if _HEREDOC_OPENER.search(line)
        and _line_is_inside_unclosed_paren_subst(line)
    ]
    assert not offenders, (
        "stage_release must not nest a here-document inside a $(...) "
        "command substitution — macOS bash 3.2.57 re-scans the heredoc "
        "body and aborts the install at parse time. Offending line(s):\n"
        + "\n".join(f"  {line!r}" for line in offenders)
    )


def test_install_sh_has_no_heredoc_inside_command_substitution() -> None:
    """Repo-wide form of the plan's antipattern grep guard: NO line in
    ``deploy/install.sh`` may open a here-document inside an unclosed
    ``$(...)``.

    The plan verified line 420 was the only such site; this pins that
    property for the whole file so a *different* future heredoc-in-$()
    (anywhere in the script) is caught before it reaches a macOS host.
    """
    assert _DEPLOY_INSTALL_SH.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_SH}"
    )
    text = _DEPLOY_INSTALL_SH.read_text(encoding="utf-8")
    offenders = [
        (lineno, line)
        for lineno, line in enumerate(text.splitlines(), start=1)
        if _HEREDOC_OPENER.search(line)
        and _line_is_inside_unclosed_paren_subst(line)
    ]
    assert not offenders, (
        "deploy/install.sh must contain no here-document nested inside a "
        "$(...) command substitution (bash 3.2.57 on macOS misparses it "
        "and fails the whole install at parse time). Redirect the "
        "heredoc's stdout to a temp file and read it back instead. "
        "Offending line(s):\n"
        + "\n".join(f"  line {n}: {line!r}" for n, line in offenders)
    )


@pytest.mark.skipif(
    not _IS_BASH_3X,
    reason="heredoc-in-$() parse bug is bash 3.x-specific; "
    "host bash is not 3.x (or bash unavailable)",
)
def test_install_sh_parses_under_host_bash_3x() -> None:
    """On a bash-3.x host (the bug's interpreter, e.g. macOS 3.2.57),
    ``deploy/install.sh`` must parse cleanly under ``bash -n``.

    The shared ``test_install_sh_parses_under_bash_n`` runs on whatever
    bash is present; on Linux CI that is bash 4/5, which never exhibits
    this bug. This variant is the *direct* execution-level pin for the
    macOS regression: it only asserts on 3.x, where a heredoc back inside
    ``$(...)`` would reproduce the ``unexpected end of file`` failure.
    """
    assert _BASH is not None
    proc = subprocess.run(
        [_BASH, "-n", str(_DEPLOY_INSTALL_SH)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"bash -n failed for {_DEPLOY_INSTALL_SH} under bash "
        f"{_BASH_VER}: {proc.stderr!r}. On bash 3.x a "
        f"here-document nested inside $(...) trips "
        f"'syntax error: unexpected end of file' — the macOS install "
        f"break this fix removes."
    )


@pytest.mark.skipif(
    not _IS_BASH_3X,
    reason="negative control only fires on bash 3.x; the buggy shape "
    "parses cleanly on bash 4+ (or bash unavailable)",
)
def test_buggy_heredoc_in_subst_reproduces_parse_error_on_bash_3x() -> None:
    """Negative control: a heredoc folded back inside ``$(...)`` —
    carrying an odd quote count in its body — MUST fail ``bash -n`` on
    bash 3.x.

    If this ever starts *passing* (the buggy shape parses), then the
    fixed-form pins above are no longer load-bearing on this interpreter
    and the guard has silently gone slack. The reproduction mirrors the
    real defect: a single-quote-bearing body (``'unknown'`` plus a
    contraction in a comment) yields an odd ``'`` count that bash 3.2
    miscounts while re-scanning the substitution.
    """
    assert _BASH is not None
    buggy = (
        'x="$(python3 - <<\'PY\'\n'
        "# this comment doesn't classify the request\n"
        "print('unknown')\n"
        "PY\n"
        ')"\n'
    )
    proc = subprocess.run(
        [_BASH, "-n", "/dev/stdin"],
        input=buggy,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, (
        "negative control failed: the buggy heredoc-in-$() shape parsed "
        "cleanly under bash "
        f"{_BASH_VER} — the parse-bug guard is no longer "
        f"load-bearing on this interpreter. stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    assert (
        "unexpected end of file" in proc.stderr
        or "matching" in proc.stderr
    ), (
        "buggy shape failed for an unexpected reason; expected the bash "
        "3.2 'unexpected end of file' / 'looking for matching quote' "
        f"parse error. stderr={proc.stderr!r}"
    )
