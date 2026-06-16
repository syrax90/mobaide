"""Pins on the post-service pairing-QR flow in ``deploy/install.sh``.

The Linux/macOS counterpart to the Windows-side checks in
``test_install_ps1.py``. The reorder makes the running agent service
the single initiator of the pairing nonce: the QR is rendered ONLY
after ``wait_agent_healthy`` confirms ``/api/health`` returns 200 and
``show_pairing_qr_from_service`` finds the pending file the
service's pairing-monitor wrote. The previous order rendered the QR
*before* the service was up, so a mobile scan during the boot gap
silently failed to land — ``RELAY_AGENT_ID`` /
``RELAY_AGENT_TOKEN`` in ``.env`` stayed empty.

These tests are pure text/AST pins on ``deploy/install.sh`` — they
do NOT execute the installer; they assert the source-of-truth file
encodes the new contract. End-to-end execution is covered by the
manual verification steps in the plan and by CI distro images.
"""
from __future__ import annotations

import re
import subprocess
import sys
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

    Robust enough for ``deploy/install.sh`` since every function in
    the file follows the canonical ``name() {{ ... }}`` shape with the
    closing ``}}`` at column 0 (matching the ``sed -n '/^name()/,/^}}$/p'``
    convention used elsewhere in this test suite — see the
    ``ensure_ensurepip_available`` extraction in
    ``test_install_script_trap.py``). A nested function in another
    function would break this — none exist here today; if any are
    added in future, this helper will need a proper parser.
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


@pytest.mark.parametrize(
    "func_name",
    [
        "read_env_value",
        "wait_agent_healthy",
        "show_pairing_qr_from_service",
    ],
)
def test_pairing_qr_helpers_are_defined(func_name: str) -> None:
    """The three new functions that implement the service-first flow
    must exist. If any is renamed or dropped, ``main()`` calls into
    the void and the installer fails opaquely instead of with a clear
    "service not healthy" / "no pending file" diagnostic.
    """
    funcs = _list_function_names()
    assert func_name in funcs, (
        f"expected function {func_name!r} in {_DEPLOY_INSTALL_SH}; "
        f"found functions: {funcs!r}. main() depends on "
        f"read_env_value, wait_agent_healthy, and "
        f"show_pairing_qr_from_service for the post-service QR render."
    )


def test_old_print_pairing_qr_function_is_removed() -> None:
    """The pre-fix ``print_pairing_qr`` must NOT be defined anymore.

    Mirrors ``test_old_write_pairing_qr_function_is_removed`` in the
    Windows-side test module. Leaving the old definition behind would
    let a future PR re-introduce the pre-service-up render that this
    rewrite eliminates.
    """
    funcs = _list_function_names()
    assert "print_pairing_qr" not in funcs, (
        f"print_pairing_qr must be removed from {_DEPLOY_INSTALL_SH} "
        f"after the service-first reorder; "
        f"show_pairing_qr_from_service replaces it. Found functions: "
        f"{funcs!r}."
    )


def test_main_orders_service_install_before_health_before_qr() -> None:
    """``main()`` must call the supervisor install (systemd or launchd)
    before ``wait_agent_healthy`` before ``show_pairing_qr_from_service``
    before ``print_completion``.

    The whole point of the rewrite is this ordering. The QR must
    render AFTER ``wait_agent_healthy`` succeeds, otherwise the
    operator's scan during the boot gap silently fails to pair and
    ``.env`` stays empty.
    """
    body = _function_body("main")
    systemd_pos = body.find("install_systemd_unit")
    launchd_pos = body.find("install_launchd_plist")
    health_pos = body.find("wait_agent_healthy")
    qr_pos = body.find("show_pairing_qr_from_service")
    completion_pos = body.find("print_completion")
    assert systemd_pos >= 0 and launchd_pos >= 0, (
        "main must call both install_systemd_unit (Linux) and "
        "install_launchd_plist (macOS). Body:\n" + repr(body)
    )
    assert health_pos >= 0, (
        "main must call wait_agent_healthy after the supervisor "
        "install. Body:\n" + repr(body)
    )
    assert qr_pos >= 0, (
        "main must call show_pairing_qr_from_service. Body:\n"
        + repr(body)
    )
    assert completion_pos >= 0, (
        "main must call print_completion. Body:\n" + repr(body)
    )
    supervisor_max = max(systemd_pos, launchd_pos)
    assert supervisor_max < health_pos < qr_pos < completion_pos, (
        f"main ordering is broken. Required order: supervisor install "
        f"(max of systemd={systemd_pos}, launchd={launchd_pos} = "
        f"{supervisor_max}) < wait_agent_healthy ({health_pos}) < "
        f"show_pairing_qr_from_service ({qr_pos}) < print_completion "
        f"({completion_pos}). The QR must render AFTER the service is "
        f"confirmed healthy, otherwise a mobile scan during the boot "
        f"gap will silently fail to pair and .env will stay empty."
    )


def test_main_no_longer_invokes_pair_qr_print() -> None:
    """No ``pair-qr --print`` call may remain in ``main()`` or in
    ``show_pairing_qr_from_service``.

    The new flow uses ``mobaide-agent show-qr --require-pending``
    (which reads the existing pending file) so the running service is
    the sole initiator of the pairing nonce. ``pair-qr --print`` was
    the pre-fix shape that ran before the service and initiated a
    parallel nonce — the bug the rewrite eliminates.
    """
    main_body = _function_body("main")
    assert "pair-qr --print" not in main_body, (
        "main must not invoke `mobaide-agent pair-qr --print` — "
        "that was the pre-fix shape that ran before the service was "
        "up. Body:\n" + repr(main_body)
    )
    qr_body = _function_body("show_pairing_qr_from_service")
    assert "pair-qr --print" not in qr_body, (
        "show_pairing_qr_from_service must not invoke `pair-qr "
        "--print` — it must render via `show-qr --require-pending` "
        "so the running service stays the sole initiator. Body:\n"
        + repr(qr_body)
    )


def test_wait_agent_healthy_polls_api_health() -> None:
    """``wait_agent_healthy`` must hit the FastAPI health route.

    Pin the actual route (``/api/health``), 127.0.0.1 (the loopback
    contact, not the bind ``HOST`` which defaults to 0.0.0.0), and
    ``curl`` (rather than ``wget`` or a Python heredoc — keeps the
    function dependency-free).
    """
    body = _function_body("wait_agent_healthy")
    assert "/api/health" in body, (
        "wait_agent_healthy must hit `/api/health` "
        "(mobaide/agent/app.py:380). Body:\n" + repr(body)
    )
    assert "127.0.0.1" in body, (
        "wait_agent_healthy must contact 127.0.0.1, not the agent's "
        "HOST (which defaults to 0.0.0.0, the bind address). Body:\n"
        + repr(body)
    )
    assert re.search(r"\bcurl\b", body), (
        "wait_agent_healthy must use curl (already a required "
        "dependency via check_command curl in main). Body:\n"
        + repr(body)
    )


def test_wait_agent_healthy_diagnostics_point_to_logs() -> None:
    """On timeout, the diagnostic must point to journalctl (Linux) /
    log file (macOS) so the operator can find the underlying
    service-start failure that this guard exposes.
    """
    body = _function_body("wait_agent_healthy")
    assert "fail" in body, (
        "wait_agent_healthy must hard-fail (call `fail`) on timeout, "
        "not silently fall through. The whole point of this guard is "
        "to surface a service crash that the supervisor returning "
        "success would otherwise mask. Body:\n" + repr(body)
    )
    assert "journalctl" in body, (
        "wait_agent_healthy must reference journalctl in the Linux "
        "diagnostic so the operator can find the service-crash trace. "
        "Body:\n" + repr(body)
    )
    assert "mobaide-agent" in body or "MOBAIDE_AGENT_PREFIX" in body, (
        "wait_agent_healthy diagnostic must name the service / install "
        "prefix so the operator can target the right logs. Body:\n"
        + repr(body)
    )


def test_show_pairing_qr_uses_require_pending() -> None:
    """``show_pairing_qr_from_service`` must invoke ``show-qr
    --require-pending`` (not ``pair-qr --print``).

    The flag prevents the CLI from initiating a second pairing nonce
    when the pending file isn't yet present. The running service is
    the sole initiator; the installer renders from the file the
    service wrote. Two concurrent /ws/pairing/{nonce} dials for the
    same nonce would race; central_server rejects the loser with WS
    close 4001 (single-use semantics).
    """
    body = _function_body("show_pairing_qr_from_service")
    assert "show-qr" in body, (
        "show_pairing_qr_from_service must call `mobaide-agent "
        "show-qr` rather than `pair-qr --print` so the CLI reads "
        "the running service's pending_pairing.json. Body:\n"
        + repr(body)
    )
    assert "--require-pending" in body, (
        "show_pairing_qr_from_service must pass `--require-pending` "
        "to show-qr so it never initiates a second nonce. Without "
        "this flag, two processes race for /ws/pairing/{nonce} and "
        "central_server rejects the loser. Body:\n" + repr(body)
    )
    assert "pending_pairing.json" in body, (
        "show_pairing_qr_from_service must wait for "
        "pending_pairing.json before rendering — the service's "
        "lifespan task is concurrent with the yield, so /api/health "
        "responds 200 before the file appears. Body:\n" + repr(body)
    )


def test_show_pairing_qr_preserves_skip_env_var() -> None:
    """``MOBAIDE_AGENT_SKIP_INSTALL_QR`` must still suppress the render.

    Documented env-var contract used by CI / unattended installs.
    """
    body = _function_body("show_pairing_qr_from_service")
    assert "MOBAIDE_AGENT_SKIP_INSTALL_QR" in body, (
        "show_pairing_qr_from_service must honour the documented "
        "MOBAIDE_AGENT_SKIP_INSTALL_QR opt-out env var. Body:\n"
        + repr(body)
    )


def test_install_sh_parses_under_bash_n() -> None:
    """``deploy/install.sh`` must parse cleanly under ``bash -n``.

    Cheap insurance against syntax breakage. Mirrors
    ``test_install_script_passes_bash_syntax_check`` in
    ``test_install_script_trap.py``; runs even on Windows when Git
    Bash is available.
    """
    # Reuse the same bash lookup as test_install_script_trap.py so
    # Windows hosts with Git Bash run this test too.
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


def test_wait_agent_healthy_info_writes_to_stderr_not_stdout() -> None:
    """``wait_agent_healthy``'s diagnostic ``info`` lines must redirect
    to stderr (``>&2``).

    ``main()`` captures this function's stdout into ``health_json``
    for JSON parsing. ``info()`` writes to stdout by default; an
    un-redirected ``info "..."`` would corrupt the captured body with
    ``[INFO] ...\\n{actual JSON}`` and break the subsequent
    ``json.load`` in ``show_pairing_qr_from_service``. The fix is to
    redirect ``info`` calls in this function to stderr.
    """
    body = _function_body("wait_agent_healthy")
    info_calls = re.findall(r"^\s*info\s+.*$", body, re.MULTILINE)
    assert info_calls, (
        "wait_agent_healthy should emit at least one info diagnostic. "
        "Body:\n" + repr(body)
    )
    unsafe = [line for line in info_calls if not line.rstrip().endswith(">&2")]
    assert not unsafe, (
        "wait_agent_healthy's info diagnostics must be redirected to "
        "stderr (`info \"...\" >&2`) — main() captures this function's "
        "stdout into $health_json and any un-redirected info line "
        "would corrupt the JSON parse downstream. Offending lines:\n"
        + "\n".join(f"  {line!r}" for line in unsafe)
    )


def test_download_asset_helper_is_defined() -> None:
    """The ``download_asset`` helper must exist.

    ``stage_release`` delegates every release-asset download to it. If it
    is renamed or dropped, ``stage_release`` calls into the void and the
    install fails opaquely instead of staging the tarball/wheel.
    """
    funcs = _list_function_names()
    assert "download_asset" in funcs, (
        f"expected function 'download_asset' in {_DEPLOY_INSTALL_SH}; "
        f"found functions: {funcs!r}. stage_release downloads the tarball "
        f"and wheel through it."
    )


def test_download_asset_uses_curl_not_python_urllib() -> None:
    """``download_asset`` must fetch over HTTPS with ``curl`` (OS-native
    trust store), carrying the installer ``-A`` User-Agent.

    This is the macOS SSL fix. A python.org framework interpreter whose
    bundled ``Install Certificates.command`` never ran has an EMPTY
    OpenSSL CA store, so Python ``urllib`` raises
    ``CERTIFICATE_VERIFY_FAILED`` while ``curl`` to the same host succeeds
    (``fetch_manifest`` + ``bootstrap_trust_key`` already prove curl
    works). Mirrors ``install.ps1``'s ``Invoke-AgentDownload``, which
    downloads via ``Invoke-WebRequest`` for the identical root cause.
    """
    body = _function_body("download_asset")
    assert re.search(r"\bcurl\b", body), (
        "download_asset must use curl for network downloads — curl uses "
        "the OS trust store; python.org urllib has an empty CA store on "
        "macOS and fails with CERTIFICATE_VERIFY_FAILED. Body:\n"
        + repr(body)
    )
    assert "-A " in body or "--user-agent" in body, (
        "download_asset must pass the installer User-Agent (curl -A) so a "
        "Cloudflare-fronted wheel host doesn't 1010-block the default UA. "
        "Body:\n" + repr(body)
    )


def test_download_asset_preserves_file_scheme_via_copy() -> None:
    """``download_asset`` must route ``file://`` / absolute-path URLs to a
    local copy (``cp``), not curl.

    Preserves the air-gapped self-host path (offline mirrors, test
    fixtures) the old Python ``fetch()`` supported via
    ``shutil.copyfile``. Mirrors ``install.ps1``'s ``Invoke-AgentDownload``
    ``file://`` branch (``Copy-Item``).
    """
    body = _function_body("download_asset")
    assert "file://" in body, (
        "download_asset must special-case file:// URLs for air-gapped / "
        "test installs. Body:\n" + repr(body)
    )
    assert re.search(r"\bcp\b", body), (
        "download_asset's file:// / absolute-path branch must copy locally "
        "with cp (curl on file:// is pointless / inconsistent). Body:\n"
        + repr(body)
    )


def test_stage_release_downloads_through_download_asset() -> None:
    """``stage_release`` must fetch BOTH assets via ``download_asset``.

    If either the tarball or the wheel is fetched another way (e.g. a
    reintroduced Python ``urllib`` call), the macOS empty-CA-store crash
    this fix removes can return for that asset.
    """
    body = _function_body("stage_release")
    assert body.count("download_asset ") >= 2, (
        "stage_release must call download_asset for the tarball AND the "
        "wheel (>= 2 calls). Body:\n" + repr(body)
    )


def test_stage_release_staging_python_does_no_urllib_network() -> None:
    """The Python staging step in ``stage_release`` must NOT touch the
    network — no ``urllib.request`` import and no ``urlopen`` call.

    Core of the macOS SSL fix: all network I/O moved to curl
    (``download_asset``), leaving Python to verify sha256 + rename the
    wheel + extract the tarball. ``urllib.parse`` (pure string parsing, no
    sockets) is still allowed for wheel-basename derivation, so the
    assertion targets the network surface specifically.
    """
    body = _function_body("stage_release")
    assert "urllib.request" not in body, (
        "stage_release's staging Python must not import urllib.request — "
        "the python.org empty-CA-store crash (CERTIFICATE_VERIFY_FAILED) "
        "comes from urllib's default-context urlopen. Network downloads "
        "now go through curl (download_asset). Body:\n" + repr(body)
    )
    assert "urlopen" not in body, (
        "stage_release's staging Python must not call urlopen — network "
        "I/O moved to curl (download_asset). Body:\n" + repr(body)
    )


_ = sys  # silence unused-import noise on hosts without Git Bash
