"""Cover the staged-binary auto-updater state machine.

The install path is stubbed via the injected ``installer`` callable — what
we exercise is signature verification, idle deferral, and the atomic swap
of ``current_version``. The manifest body itself is passed in directly
(no HTTP fetch in the relay-driven update path).
"""
from __future__ import annotations

import base64
import logging
import os
import sys
import tarfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobaide.agent.updater import (
    _STALE_SCRATCH_SECONDS,
    InstallLayout,
    StagedBinaryUpdater,
    _assert_safe_version,
    _install_wheel_into_venv,
    _is_version_newer,
    _version_tuple,
    _wheel_basename_from_url,
)
from mobaide.manifest_signing import canonical_json


def _signed_manifest(temp_files_dir: Path):
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    public_key_b64 = base64.b64encode(public_bytes).decode()

    tarball_path = temp_files_dir / "agent.tar.gz"
    wheel_path = temp_files_dir / "mobaide_agent-0.2.0-py3-none-any.whl"
    tarball_path.parent.mkdir(parents=True, exist_ok=True)

    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="manifest.txt")
        data = b"hello"
        info.size = len(data)
        tar.addfile(info, BytesIO(data))
    tarball_path.write_bytes(buf.getvalue())
    wheel_path.write_bytes(b"PK\x03\x04 fake wheel")

    import hashlib

    body = {
        "version": "0.2.0",
        "tarball_url": f"file://{tarball_path}",
        "tarball_sha256": hashlib.sha256(tarball_path.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wheel_path}",
        "wheel_sha256": hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    signature = private_key.sign(canonical_json(body))
    signature_b64 = base64.b64encode(signature).decode()
    return public_key_b64, body, signature_b64


@pytest.fixture
def layout(tmp_path: Path) -> InstallLayout:
    layout = InstallLayout(prefix=tmp_path / "mobaide")
    layout.releases_dir.mkdir(parents=True, exist_ok=True)
    layout.trust_dir.mkdir(parents=True, exist_ok=True)
    return layout


def _fake_installer(venv_dir: Path, wheel_path: Path) -> None:
    """Stub the wheel install: create the venv interpreter the updater's
    pre-swap verification (``_release_venv_python``) probes for, mirroring the
    layout ``conftest.make_release`` builds (``Scripts`` on Windows, ``bin``
    elsewhere)."""
    if sys.platform == "win32":
        bin_dir = venv_dir / "Scripts"
        python = bin_dir / "python.exe"
    else:
        bin_dir = venv_dir / "bin"
        python = bin_dir / "python"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\nexec /bin/true\n", encoding="utf-8")


async def test_no_change_when_versions_match(layout) -> None:
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.2.0",
        installer=lambda venv, wheel: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "no_change"


async def test_signature_failed_blocks_swap(layout) -> None:
    _, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=base64.b64encode(b"\x00" * 32).decode(),
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "signature_failed"
    assert not (layout.releases_dir / "0.2.0").exists()
    assert not layout.current_version_file.exists()


async def test_deferred_when_session_active(layout, monkeypatch) -> None:
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    busy_session = MagicMock()
    busy_session.state = "running"
    sm = MagicMock()
    sm.list_sessions.return_value = [busy_session]

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=sm,
        current_version="0.1.0",
        installer=lambda venv, wheel: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "deferred"


async def test_staged_swap_succeeds(layout, monkeypatch) -> None:
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    exits: list[int] = []

    def fake_exit(code: int) -> None:
        exits.append(code)

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=_fake_installer,
        exit_func=fake_exit,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )
    assert outcome == "staged"
    assert (layout.releases_dir / "0.2.0").is_dir()
    assert layout.current_version_file.read_text().strip() == "0.2.0"
    assert exits == [75]


async def test_forward_only_skips_older_version_without_force(layout) -> None:
    """Auto/poll updates are forward-only: a manifest OLDER than the running
    version is a no_change, never a (destructive) re-stage. This is the guard
    against the dev-run clobber (a newer source build vs an older manifest)."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    installer_calls: list = []
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.3.0",  # newer than the manifest's 0.2.0
        installer=lambda venv, wheel: installer_calls.append(venv),
    )
    outcome = await updater.consider_manifest(body, signature_b64, body["version"])
    assert outcome == "no_change"
    assert installer_calls == []
    assert not (layout.releases_dir / "0.2.0").exists()


async def test_failed_force_restage_preserves_running_release(layout) -> None:
    """The original brick: a force re-stage of the CURRENT version whose build
    FAILS must NOT destroy the running release (no rmtree-before-build) and
    must leave no scratch dirs behind."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    live = layout.releases_dir / "0.2.0"
    (live / "venv" / "bin").mkdir(parents=True)
    (live / "venv" / "bin" / "python").write_text("running", encoding="utf-8")

    def _boom(venv_dir, wheel_path):
        raise RuntimeError("install failed mid-stage")

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.2.0",
        installer=_boom,
        exit_func=lambda code: None,
    )
    outcome = await updater.force_update_now(
        body, signature_b64, body["version"], exit_after_stage=False,
    )
    assert outcome == "signature_failed"
    # Running release intact — never rmtree'd before the new build succeeded.
    assert (live / "venv" / "bin" / "python").read_text() == "running"
    assert not list(layout.releases_dir.glob(".staging-*"))
    assert not list(layout.releases_dir.glob(".old-*"))


async def test_force_restage_existing_version_swaps_in_new_build(layout) -> None:
    """A force re-stage of an existing version that SUCCEEDS atomically swaps in
    the freshly-built release (old contents replaced) and cleans the backup."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    live = layout.releases_dir / "0.2.0"
    (live / "venv" / "bin").mkdir(parents=True)
    (live / "venv" / "bin" / "python").write_text("old", encoding="utf-8")
    (live / "stale_marker").write_text("x", encoding="utf-8")

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.2.0",
        installer=_fake_installer,
        exit_func=lambda code: None,
    )
    outcome = await updater.force_update_now(
        body, signature_b64, body["version"], exit_after_stage=False,
    )
    assert outcome == "staged"
    # New build swapped in: the old-only marker is gone, the venv exists, and
    # no backup scratch dir lingers.
    assert not (live / "stale_marker").exists()
    assert (live / "venv" / "bin" / "python").exists()
    assert not list(layout.releases_dir.glob(".old-*"))


@pytest.mark.parametrize(
    "bad",
    [
        "../evil", "..", ".", "a/b", "a\\b", "x\x00y", "",
        # Windows drive-relative escape: ``releases / "z:evil"`` drops the
        # anchor. The allowlist rejects ``:`` (and any other metacharacter).
        "z:evil", "a:b:c", "a;b", "a b", "$(x)",
    ],
)
def test_assert_safe_version_rejects_path_escapes(bad: str) -> None:
    with pytest.raises(ValueError, match="safe path component"):
        _assert_safe_version(bad)


@pytest.mark.parametrize("ok", ["0.2.13", "0.2.0rc1", "1.0.0+build", "0.2.0.dev1"])
def test_assert_safe_version_allows_real_versions(ok: str) -> None:
    _assert_safe_version(ok)  # must not raise


async def test_force_stage_rejects_path_escaping_version(layout) -> None:
    """A signed-but-malicious version must be rejected before it becomes a
    releases/<v> path component — defense-in-depth at the manifest->fs boundary.
    Force bypasses the forward-only gate, so this reaches _stage_release where
    the guard fires."""
    import hashlib

    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode()
    tmp = layout.prefix / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    tb = tmp / "agent.tar.gz"
    wh = tmp / "mobaide_agent-9.9.9-py3-none-any.whl"
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="x")
        info.size = 1
        tar.addfile(info, BytesIO(b"x"))
    tb.write_bytes(buf.getvalue())
    wh.write_bytes(b"PK\x03\x04")
    body = {
        "version": "../../../tmp/mobaide-escape",
        "tarball_url": f"file://{tb}",
        "tarball_sha256": hashlib.sha256(tb.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wh}",
        "wheel_sha256": hashlib.sha256(wh.read_bytes()).hexdigest(),
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    sig = base64.b64encode(private_key.sign(canonical_json(body))).decode()
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=_fake_installer,
        exit_func=lambda code: None,
    )
    outcome = await updater.force_update_now(
        body, sig, body["version"], exit_after_stage=False,
    )
    assert outcome == "signature_failed"
    # No directory created outside releases/.
    assert not (layout.prefix.parent / "mobaide-escape").exists()
    assert not (layout.prefix / "mobaide-escape").exists()


def test_sweep_preserves_concurrent_other_pid_scratch(layout) -> None:
    """The scratch sweep removes this process's own leftovers but must NOT
    delete a concurrent process's recent in-flight staging dir."""
    releases = layout.releases_dir
    own = releases / f".staging-0.2.0-{os.getpid()}"
    own.mkdir()
    other_recent = releases / f".staging-0.2.0-{os.getpid() + 1}"
    other_recent.mkdir()
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64="x",
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel: None,
    )
    updater._sweep_scratch_dirs()
    assert not own.exists(), "own-pid scratch should be swept"
    assert other_recent.exists(), (
        "a concurrent other-pid recent scratch must be preserved (no cross-"
        "process corruption)"
    )


def test_sweep_removes_stale_other_pid_scratch(layout) -> None:
    """A scratch dir old enough that its creator is certainly gone is GC'd even
    if its pid is not this process's."""
    import time as _time

    releases = layout.releases_dir
    stale = releases / f".old-0.2.0-{os.getpid() + 999}"
    stale.mkdir()
    old = _time.time() - (_STALE_SCRATCH_SECONDS + 100)
    os.utime(stale, (old, old))
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64="x",
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel: None,
    )
    updater._sweep_scratch_dirs()
    assert not stale.exists(), "stale other-pid scratch should be GC'd"


async def test_non_dict_body_fails_closed(layout) -> None:
    """A garbage payload arriving on the WebSocket (e.g. a serialised
    list or a stray string) must short-circuit before reaching
    ``verify_signature`` rather than blowing up with a TypeError."""
    public_key_b64 = base64.b64encode(b"\x00" * 32).decode()
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel: None,
    )
    outcome = await updater.consider_manifest(
        ["not", "a", "dict"],  # type: ignore[arg-type]
        "sig",
        "0.2.0",
    )
    assert outcome == "signature_failed"
    assert not layout.current_version_file.exists()


async def test_staging_rejects_non_pep427_wheel_url(layout) -> None:
    # pip 25.2+ enforces PEP 427 wheel-filename validation. A manifest whose
    # `wheel_url` doesn't end in a compliant `name-version-...-platform.whl`
    # must be rejected fail-closed BEFORE pip is invoked, otherwise the bare
    # `agent.whl` placeholder used by older versions would pass through and
    # blow up at install time. The catch-all in ``consider_manifest`` maps
    # ``ValueError`` from ``_stage_release`` to ``"signature_failed"``.
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()

    temp_files_dir = layout.prefix / "tmp"
    temp_files_dir.mkdir(parents=True, exist_ok=True)
    tarball_path = temp_files_dir / "agent.tar.gz"
    wheel_path = temp_files_dir / "agent.whl"
    import hashlib

    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="manifest.txt")
        data = b"hello"
        info.size = len(data)
        tar.addfile(info, BytesIO(data))
    tarball_path.write_bytes(buf.getvalue())
    wheel_path.write_bytes(b"PK\x03\x04 fake wheel")

    body = {
        "version": "0.2.0",
        "tarball_url": f"file://{tarball_path}",
        "tarball_sha256": hashlib.sha256(tarball_path.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wheel_path}",
        "wheel_sha256": hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    sig_b64 = base64.b64encode(private_key.sign(canonical_json(body))).decode()

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel: None,
    )
    outcome = await updater.consider_manifest(
        body, sig_b64, body["version"],
    )
    assert outcome == "signature_failed"
    assert not (layout.releases_dir / "0.2.0").exists()
    assert not layout.current_version_file.exists()


def test_wheel_basename_from_url_extracts_canonical_name() -> None:
    # Production CDN URL — the canonical case the release workflow emits.
    # Query strings (e.g. signed download tokens) must not leak into the
    # filename pip sees.
    url = "https://cdn.example.com/releases/mobaide_agent-0.2.0-py3-none-any.whl"
    assert _wheel_basename_from_url(url) == "mobaide_agent-0.2.0-py3-none-any.whl"

    signed = (
        "https://cdn.example.com/releases/"
        "mobaide_agent-1.4.2-cp312-cp312-manylinux2014_x86_64.whl"
        "?Signature=abc123&Expires=99999"
    )
    assert (
        _wheel_basename_from_url(signed)
        == "mobaide_agent-1.4.2-cp312-cp312-manylinux2014_x86_64.whl"
    )


@pytest.mark.parametrize(
    "url",
    [
        # Wrong extension entirely — exercises the `endswith(".whl")` branch
        # which the integration-level rejection test doesn't reach (it uses
        # `agent.whl`, which passes the extension check but fails on dashes).
        "https://cdn.example.com/releases/mobaide_agent-0.2.0.tar.gz",
        "https://cdn.example.com/releases/mobaide_agent-0.2.0",
        # Compliant extension, insufficient dashes (PEP 427 minimum is 4).
        "https://cdn.example.com/releases/agent.whl",
        "https://cdn.example.com/releases/foo-bar.whl",
        "https://cdn.example.com/releases/foo-bar-baz.whl",
        "https://cdn.example.com/releases/foo-1.0-py3.whl",
    ],
)
def test_wheel_basename_from_url_rejects_non_pep427(url: str) -> None:
    with pytest.raises(ValueError, match="non-PEP427 filename"):
        _wheel_basename_from_url(url)


def test_wheel_basename_from_url_error_includes_diagnostic_url() -> None:
    # The docstring guarantees the original URL is surfaced in the exception
    # so install-time failures can be traced back to the manifest field that
    # produced them. Pin both the offending basename and the source URL.
    bad = "https://cdn.example.com/releases/agent.whl"
    with pytest.raises(ValueError) as exc_info:
        _wheel_basename_from_url(bad)
    msg = str(exc_info.value)
    assert "agent.whl" in msg
    assert bad in msg


@pytest.mark.parametrize(
    "url",
    [
        # Backslash path-traversal vector: posixpath.basename leaves `\`
        # literal, so without the separator-char guard `Path(tmpdir) / name`
        # would resolve outside ``tmpdir`` on Windows and let `_download_to`
        # write the wheel body to an attacker-chosen location.
        "https://attacker.example/legit/..\\..\\..\\Windows\\System32\\evil-1-2-3-4.whl",
        # NUL byte injection — would be silently truncated by C-string APIs
        # downstream of pip.
        "https://attacker.example/legit/foo\x00bar-1.0-py3-none-any.whl",
    ],
)
def test_wheel_basename_from_url_rejects_path_separators(url: str) -> None:
    with pytest.raises(ValueError, match="path-separator chars"):
        _wheel_basename_from_url(url)


class _FakeBytesResp:
    def __init__(self, body: bytes = b"manifest-body"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        if n == -1 or n >= len(self._body):
            out, self._body = self._body, b""
            return out
        out, self._body = self._body[:n], self._body[n:]
        return out


async def test_consider_manifest_apply_path_never_calls_urlopen(
    layout, monkeypatch,
) -> None:
    """Regression guard for the deleted ``_download_manifest`` HTTP fetch.

    The original bug was a relative ``manifest_url`` ("/api/v1/agent/manifest.json")
    being read as a local filesystem path inside ``_download_manifest``, which
    silently failed-closed to ``"signature_failed"``. The fix deletes that
    function entirely and delivers the body inline over the relay WebSocket.

    This test pins the *architectural* invariant the fix relies on: the
    manifest-apply path (``consider_manifest`` -> stage release -> swap) must
    not, under any circumstance, perform an HTTP fetch for the manifest. We
    enforce that by patching ``_http.urlopen`` to raise — and feeding the
    updater ``file://`` tarball/wheel URLs so the legitimate asset-download
    branch short-circuits inside ``_download_to`` without ever calling
    ``urlopen``. If a future change re-introduces any HTTP fetch on the
    manifest path (a re-fetch for "freshness", a CDN warm-up, etc.) this
    test fails loudly instead of producing a fail-closed signature_failed at
    runtime against real users.
    """
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    urlopen_calls: list[str] = []

    def _urlopen_must_not_be_called(*args, **kwargs):
        urlopen_calls.append(repr((args, kwargs)))
        raise AssertionError(
            "manifest-apply path must not call _http.urlopen — body is "
            "delivered inline over the relay WebSocket (see plan: 'eliminates "
            "the entire _download_manifest HTTP path')"
        )

    monkeypatch.setattr(
        "mobaide.agent.updater._http.urlopen", _urlopen_must_not_be_called
    )

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=_fake_installer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, body["version"],
    )

    assert outcome == "staged", (
        f"expected 'staged' on file:// asset paths; got {outcome!r}. The most "
        f"likely cause is a regression that introduced an HTTP fetch on the "
        f"manifest path. urlopen_calls={urlopen_calls!r}"
    )
    assert urlopen_calls == [], (
        f"manifest-apply path called _http.urlopen — regression to pre-fix "
        f"HTTP-fetch model. Calls captured: {urlopen_calls!r}"
    )


async def test_force_update_now_apply_path_never_calls_urlopen(
    layout, monkeypatch,
) -> None:
    """Same invariant as the previous test, but exercising the
    ``force_update_now`` entry point (the one wired to ``POST
    /api/agent/update/force_now`` — the user-facing "Install now" CTA that
    triggered the original production bug).

    Force-update bypasses idle-deferral but goes through the exact same
    ``consider_manifest`` body — a regression that adds an HTTP fetch only
    inside the force path (rather than the watch-loop path) would still
    reproduce the original UX failure even if the previous test stayed
    green.
    """
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    urlopen_calls: list[str] = []

    def _urlopen_must_not_be_called(*args, **kwargs):
        urlopen_calls.append(repr((args, kwargs)))
        raise AssertionError("force_update_now must not HTTP-fetch the manifest")

    monkeypatch.setattr(
        "mobaide.agent.updater._http.urlopen", _urlopen_must_not_be_called
    )

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=_fake_installer,
        exit_func=lambda code: None,
    )
    outcome = await updater.force_update_now(
        body, signature_b64, body["version"], exit_after_stage=False,
    )

    assert outcome == "staged"
    assert urlopen_calls == []


def test_download_to_routes_through_user_agent_wrapper(tmp_path) -> None:
    # Regression guard for the Cloudflare 1010 UA block on the wheel/tarball download path. The
    # ``file://`` short-circuit in ``_download_to`` means existing tests
    # never touch ``urllib`` — pin the http(s) branch explicitly so a
    # future revert can't slip past unnoticed. The manifest itself is no
    # longer fetched over HTTP from the relay-driven update path (body
    # arrives inline over the WebSocket), so the matching
    # ``_http_download`` UA guard moved with the function deletion.
    captured: dict = {}
    payload = b"PK\x03\x04 fake wheel bytes"

    def _fake_urlopen(req, timeout=None, context=None):
        captured["request"] = req
        return _FakeBytesResp(payload)

    dest = tmp_path / "agent.whl"
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        StagedBinaryUpdater._download_to(
            "https://cdn.example.com/releases/mobaide_agent-0.2.0-py3-none-any.whl",
            dest,
        )

    assert dest.read_bytes() == payload
    ua = captured["request"].get_header("User-agent")
    assert ua is not None and ua.startswith("mobaide-agent/")


class TestInstallWheelIntoVenvHidesWindow:
    """Pin the Windows ``CREATE_NO_WINDOW`` flag on every ``subprocess.run``
    invocation in ``_install_wheel_into_venv``.

    The updater runs inside the long-lived agent process. The agent
    runs under ``pythonw.exe`` via the per-user Scheduled Task (no
    parent console). A default ``subprocess.run`` of a console-mode
    child like ``python -m venv`` or ``pip install`` would therefore
    pop a fresh console window into the user's session on every
    auto-update. ``CREATE_NO_WINDOW`` (0x08000000) suppresses that
    while preserving ``capture_output=True``'s stdio inheritance
    (unlike ``DETACHED_PROCESS``).

    The flag is module-level ``_WINDOWS_NO_WINDOW``: equals
    ``subprocess.CREATE_NO_WINDOW`` on win32, ``0`` elsewhere. Tests
    monkeypatch ``subprocess.run`` so they run on every host
    regardless of whether the actual venv/pip invocations would
    succeed.
    """

    def _capture_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> list[dict]:
        """Wire a deterministic ``subprocess.run`` recorder onto the
        updater module's bound ``subprocess`` reference (mirrors the
        pattern in ``test_env_writer.TestRestrictPermsWindowsAclArgv``).
        """
        calls: list[dict] = []

        def fake_run(cmd, **kwargs):
            calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            return MagicMock(returncode=0, stdout=b"", stderr=b"")

        import mobaide.agent.updater as _updater_mod

        monkeypatch.setattr(_updater_mod.subprocess, "run", fake_run)
        return calls

    def test_all_three_subprocess_run_calls_pass_creationflags(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        # _install_wheel_into_venv makes three subprocess.run calls:
        #   1. python -m venv <venv_dir>
        #   2. <venv>/Scripts/pip.exe install --upgrade pip
        #   3. <venv>/Scripts/pip.exe install <wheel>
        # All three MUST pass ``creationflags=_WINDOWS_NO_WINDOW``.
        # Without any one, that subprocess pops a console window on
        # Windows on every auto-update cycle.
        calls = self._capture_run(monkeypatch)
        import mobaide.agent.updater as _updater_mod

        venv_dir = tmp_path / "venv"
        wheel_path = tmp_path / "mobaide_agent-0.2.0-py3-none-any.whl"
        wheel_path.write_bytes(b"PK\x03\x04 fake")

        _install_wheel_into_venv(venv_dir, wheel_path)

        assert len(calls) == 3, (
            f"expected exactly 3 subprocess.run calls (venv, pip "
            f"upgrade, wheel install); got {len(calls)}"
        )

        expected_flag = _updater_mod._WINDOWS_NO_WINDOW
        for idx, call in enumerate(calls):
            flag = call["kwargs"].get("creationflags")
            assert flag == expected_flag, (
                f"subprocess.run call #{idx + 1} "
                f"(cmd={call['cmd']!r}) must pass "
                f"creationflags=_WINDOWS_NO_WINDOW ({expected_flag!r}); "
                f"got creationflags={flag!r}. On Windows this means "
                f"the call would pop a console window for the "
                f"subprocess (pip / venv / python.exe child)"
            )

    def test_windows_no_window_constant_resolves_correctly(self) -> None:
        """On win32, ``_WINDOWS_NO_WINDOW`` MUST equal
        ``subprocess.CREATE_NO_WINDOW`` (0x08000000). On every other
        platform it MUST be 0 so the flag is a no-op (CPython's
        Linux/macOS ``subprocess`` ignores unknown ``creationflags``
        values, but a non-zero literal on non-Windows is a sign the
        constant has drifted from its intended platform-conditional
        shape).
        """
        import mobaide.agent.updater as _updater_mod

        if sys.platform == "win32":
            import subprocess as _sp
            assert _updater_mod._WINDOWS_NO_WINDOW == _sp.CREATE_NO_WINDOW
            assert _updater_mod._WINDOWS_NO_WINDOW == 0x08000000, (
                "the literal value MUST be 0x08000000 -- if Python's "
                "subprocess module changes the symbol's value this "
                "assertion will surface that ABI break before it ships"
            )
        else:
            assert _updater_mod._WINDOWS_NO_WINDOW == 0


@pytest.mark.parametrize(
    "version, expected",
    [
        ("0.2.0", (0, 2, 0)),
        # PEP 440 local/build metadata after `+` is stripped.
        ("0.2.13+local", (0, 2, 13)),
        # Pre-release metadata after the first `-` is stripped.
        ("1.4.2-rc1", (1, 4, 2)),
        # Parsing stops at the first non-numeric dotted component.
        ("1.2.dev3", (1, 2)),
        ("2.0.0a1", (2, 0)),
        # Nothing numeric -> empty tuple (the "unparseable" signal the
        # forward-only gate treats conservatively).
        ("garbage", ()),
        ("", ()),
        ("v1.2.3", ()),
    ],
)
def test_version_tuple_strips_metadata_and_stops_at_non_numeric(
    version: str, expected: tuple[int, ...]
) -> None:
    assert _version_tuple(version) == expected


@pytest.mark.parametrize(
    "candidate, current, expected",
    [
        ("0.2.1", "0.2.0", True),
        ("0.3.0", "0.2.9", True),
        # Numeric (not lexical) comparison of components.
        ("0.10.0", "0.9.0", True),
        ("0.2.0", "0.2.0", False),
        ("0.2.0", "0.3.0", False),
        # Conservative: an unparseable candidate is never "newer", so a
        # malformed manifest version cannot drive a stage on the auto path.
        ("garbage", "0.2.0", False),
        ("", "0.2.0", False),
        # Conservative: an unparseable current also blocks (we can't prove
        # the candidate is forward).
        ("0.3.0", "garbage", False),
    ],
)
def test_is_version_newer_is_strict_and_conservative(
    candidate: str, current: str, expected: bool
) -> None:
    assert _is_version_newer(candidate, current) is expected


async def test_forward_only_skips_unparseable_version_without_force(
    layout, caplog
) -> None:
    """An unparseable manifest version on the non-forced path is a no_change
    (conservative ``_is_version_newer`` -> False), never a stage, and it emits
    the ``updater.forward_only_skipped`` INFO breadcrumb so operators can see
    why a poll/auto update did nothing. The gate fires on the ``latest_version``
    argument before signature verification, so the signed body is irrelevant
    here -- a malformed version string can never reach the destructive stage."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")
    installer_calls: list = []
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.2.0",
        installer=lambda venv, wheel: installer_calls.append(venv),
    )

    with caplog.at_level(logging.INFO, logger="mobaide.agent.updater"):
        outcome = await updater.consider_manifest(body, signature_b64, "garbage")

    assert outcome == "no_change"
    assert installer_calls == []
    assert not (layout.releases_dir / "garbage").exists()
    skip_logs = [
        r.getMessage()
        for r in caplog.records
        if r.name == "mobaide.agent.updater" and r.levelno == logging.INFO
    ]
    assert any("updater.forward_only_skipped" in m for m in skip_logs), (
        f"expected a forward_only_skipped INFO breadcrumb; got {skip_logs!r}"
    )


def test_swap_into_place_restores_backup_when_forward_replace_fails(
    layout, monkeypatch, caplog
) -> None:
    """The data-loss-prevention branch the integration tests don't reach: when
    ``releases/<v>`` already exists, ``_swap_into_place`` moves it aside to a
    ``.old-*`` backup and only then forward-replaces it with the staging dir.
    If that forward ``os.replace`` raises, the backup MUST be restored to the
    original release path (so the running release is never left missing) and the
    error re-raised for the caller's cleanup. The success path is already
    covered by ``test_force_restage_existing_version_swaps_in_new_build``; this
    pins the failure-restore half of the all-or-nothing contract."""
    release_dir = layout.releases_dir / "0.2.0"
    (release_dir / "venv" / "bin").mkdir(parents=True)
    (release_dir / "venv" / "bin" / "python").write_text("running", encoding="utf-8")

    staging_dir = layout.releases_dir / ".staging-0.2.0-99999"
    (staging_dir / "venv" / "bin").mkdir(parents=True)
    (staging_dir / "venv" / "bin" / "python").write_text("new", encoding="utf-8")

    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def flaky_replace(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        # Let the move-aside (release -> backup) succeed, then blow up on the
        # forward move (staging -> release) that the restore logic guards.
        if Path(src) == staging_dir and Path(dst) == release_dir:
            raise OSError("simulated cross-device / locked-file failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", flaky_replace)

    with caplog.at_level(logging.ERROR, logger="mobaide.agent.updater"):
        with pytest.raises(OSError, match="simulated"):
            StagedBinaryUpdater._swap_into_place(staging_dir, release_dir)

    # Restore happened: the original release is back in place with its content,
    # and no `.old-*` backup is left orphaned at the release path.
    assert (release_dir / "venv" / "bin" / "python").read_text() == "running"
    assert not list(layout.releases_dir.glob(".old-*"))
    # Restore succeeded, so the "restore failed" loud-error path must NOT fire.
    restore_failures = [
        r.getMessage()
        for r in caplog.records
        if "updater.swap_restore_failed" in r.getMessage()
    ]
    assert restore_failures == []


async def test_stage_sweeps_stale_scratch_dirs_before_building(layout) -> None:
    """A stage killed mid-build can leave ``.staging-*`` / ``.old-*`` scratch
    dirs behind. ``_stage_release`` sweeps them at entry (``_sweep_scratch_dirs``)
    so they don't accumulate across attempts; a successful stage then writes the
    real ``releases/<v>``. The scratch names never parse as a version, so this
    is purely about keeping ``releases/`` tidy -- but a regression that dropped
    the sweep would silently leak directories on every recovered update."""
    public_key_b64, body, signature_b64 = _signed_manifest(layout.prefix / "tmp")

    stale_staging = layout.releases_dir / f".staging-0.1.9-{os.getpid()}"
    stale_old = layout.releases_dir / f".old-0.1.9-{os.getpid()}"
    for scratch in (stale_staging, stale_old):
        scratch.mkdir(parents=True)
        (scratch / "leftover").write_text("x", encoding="utf-8")

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=_fake_installer,
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(body, signature_b64, body["version"])

    assert outcome == "staged"
    assert (layout.releases_dir / "0.2.0").is_dir()
    # Both pre-existing scratch dirs were swept at stage start.
    assert not stale_staging.exists()
    assert not stale_old.exists()
    assert not list(layout.releases_dir.glob(".staging-*"))
    assert not list(layout.releases_dir.glob(".old-*"))
