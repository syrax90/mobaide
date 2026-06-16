"""Behavioural pins for the curl-download + network-free staging path that
the macOS SSL fix introduced in ``deploy/install.sh``.

The sibling modules (``test_install_sh.py``,
``test_install_sh_heredoc_no_subst.py``) are pure text/AST pins: they
assert the source-of-truth *shape* of ``download_asset`` /
``stage_release`` but never *execute* the new code. That leaves the
load-bearing behaviour of this fix unverified by automation — the plan's
own verification step 3 (local end-to-end with a ``file://`` manifest,
no network) was a manual step.

These tests close that gap WITHOUT touching the network. They run the
actual shipped code on the actual interpreter:

* ``download_asset`` is sourced from ``deploy/install.sh`` (a plain-file
  ``source``, NOT ``source <(sed ...)`` — process-substitution sourcing
  silently no-ops on macOS bash 3.2.57, the very interpreter this fix
  targets) and exercised over its ``file://`` / absolute-path / failure
  branches.
* The staging Python block is extracted verbatim from ``stage_release``
  and driven argv-first against a synthetic manifest whose ``*_url``
  point at local ``file://`` assets (same shape as
  ``test_e2e_autoupdate.py::_sign_manifest``). It must rename the
  curl-staged ``wheel.download`` to its canonical PEP 427 name, verify
  both sha256 digests, extract the tarball, and print the canonical wheel
  path — the exact contract ``stage_release`` relies on between the curl
  download and the ``pip install "$wheel_path"`` line.

Only ``download_asset`` + the staging Python are exercised — NOT the
whole ``stage_release``, whose trailing ``python3 -m venv`` / ``pip
install`` is platform-coupled and predates this fix. Auto-skipped where
bash is unavailable; the staging-Python checks are bash-independent and
run everywhere with a Python 3 on PATH.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tarfile
from functools import cache
from io import BytesIO
from pathlib import Path

import pytest

from test_install_script_trap import _BASH


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"

_STAGING_INVOCATION = 'python3 - "$manifest_path" "$tmpdir" "$release_dir" >'


@cache
def _install_sh_text() -> str:
    assert _DEPLOY_INSTALL_SH.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_SH}"
    )
    return _DEPLOY_INSTALL_SH.read_text(encoding="utf-8")


@cache
def _download_asset_definition() -> str:
    """The verbatim ``download_asset() { ... }`` definition from install.sh.

    Extracted by the canonical ``\\n<name>() {\\n`` start + matching
    column-0 ``\\n}`` end (the same shape ``_sh_function_body`` keys on),
    so the harness sources the *shipped* helper, not a hand-copied one.
    """
    text = _install_sh_text()
    needle = "\ndownload_asset() {\n"
    start = text.find(needle)
    assert start != -1, (
        f"download_asset() not found in {_DEPLOY_INSTALL_SH} — the macOS "
        f"SSL fix must define it (sibling pin "
        f"test_download_asset_helper_is_defined guards the name)."
    )
    end = text.find("\n}\n", start + len(needle))
    assert end != -1, (
        f"download_asset() has no column-0 closing brace in "
        f"{_DEPLOY_INSTALL_SH}"
    )
    return text[start + 1 : end + 2]


@cache
def _staging_python_source() -> str:
    """The verbatim Python body of ``stage_release``'s staging heredoc.

    Anchored on the unique staging invocation line (``python3 -
    "$manifest_path" "$tmpdir" "$release_dir" > ...``) so it cannot pick
    up either of the other two ``<<'PY'`` heredocs in the script. Running
    this exact text proves the rename + verify + extract contract the fix
    moved into Python actually holds — something the ``"urlopen" not in
    body`` text pin cannot show.
    """
    text = _install_sh_text()
    invoke = text.find(_STAGING_INVOCATION)
    assert invoke != -1, (
        f"staging invocation {_STAGING_INVOCATION!r} not found in "
        f"{_DEPLOY_INSTALL_SH} — stage_release must run the network-free "
        f"staging heredoc with argv (manifest, tmpdir, release_dir)."
    )
    opener = text.find("<<'PY'\n", invoke)
    assert opener != -1, (
        "staging invocation is not followed by a <<'PY' heredoc opener"
    )
    body_start = opener + len("<<'PY'\n")
    body_end = text.find("\nPY\n", body_start)
    assert body_end != -1, "staging heredoc has no closing PY delimiter"
    return text[body_start:body_end]


def _build_assets(tmp: Path, version: str) -> tuple[Path, Path, str]:
    """Synthetic tarball + placeholder wheel for a fake agent release.

    Mirrors ``test_e2e_autoupdate.py::_build_tarball_and_wheel``: the
    tarball carries one ``mobaide/__init__.py`` pinning ``__version__``;
    the wheel is a placeholder (staging never installs it). Returns
    ``(tarball, wheel, canonical_wheel_name)``.
    """
    tarball = tmp / f"agent-{version}.tar.gz"
    wheel_name = f"mobaide_agent-{version}-py3-none-any.whl"
    wheel = tmp / wheel_name

    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = f'__version__ = "{version}"\n'.encode()
        info = tarfile.TarInfo(name="mobaide/__init__.py")
        info.size = len(payload)
        tar.addfile(info, BytesIO(payload))
    tarball.write_bytes(buf.getvalue())
    wheel.write_bytes(b"PK\x03\x04 placeholder wheel bytes")
    return tarball, wheel, wheel_name


def _write_manifest(
    tmp: Path,
    tarball: Path,
    wheel: Path,
    *,
    corrupt_wheel_sha: bool = False,
) -> Path:
    """A signed-record-shaped manifest with ``file://`` asset URLs.

    Same envelope as ``test_e2e_autoupdate.py::_sign_manifest`` minus a
    real Ed25519 signature: the staging step verifies sha256 only (the
    manifest signature is checked earlier in ``main`` by
    ``verify_manifest_signature``, outside this contract), so a
    placeholder ``signature_b64`` is sufficient and keeps the test free
    of a signing dependency.
    """
    body = {
        "version": "9.9.9",
        "tarball_url": f"file://{tarball}",
        "tarball_sha256": hashlib.sha256(tarball.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wheel}",
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }
    if corrupt_wheel_sha:
        body["wheel_sha256"] = "0" * 64
    record = {
        "body": body,
        "signature_b64": base64.b64encode(b"placeholder").decode(),
    }
    manifest = tmp / "manifest.json"
    manifest.write_text(json.dumps(record))
    return manifest


def _run_download_asset(
    tmp: Path, url: str, dest: Path
) -> subprocess.CompletedProcess[str]:
    """Source ``download_asset`` from a plain file and call it once.

    Plain-file ``source`` (not ``source <(sed ...)``): on macOS bash
    3.2.57 process-substitution sourcing silently fails to define the
    function, so the trap-module harness can't run there. A real file
    keeps this test load-bearing on the bug's own interpreter.
    """
    assert _BASH is not None
    fn_file = tmp / "download_asset.sh"
    fn_file.write_text(_download_asset_definition(), encoding="utf-8")
    harness = (
        "set -euo pipefail\n"
        'fail() { printf "[ERROR] %s\\n" "$*" >&2; exit 1; }\n'
        f"source '{fn_file.as_posix()}'\n"
        f"download_asset '{url}' '{dest.as_posix()}' 'mobaide-agent-installer/9.9.9'\n"
    )
    return subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True
    )


def _run_staging(
    manifest: Path, stage_dir: Path, release_dir: Path
) -> subprocess.CompletedProcess[str]:
    """Run the extracted staging Python with the real argv contract."""
    script = stage_dir.parent / "staging_extracted.py"
    script.write_text(_staging_python_source(), encoding="utf-8")
    return subprocess.run(
        [
            "python3",
            str(script),
            str(manifest),
            str(stage_dir),
            str(release_dir),
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(_BASH is None, reason="bash unavailable on this platform")
def test_download_asset_file_scheme_copies_payload(tmp_path: Path) -> None:
    """``download_asset`` on a ``file://`` URL must land the exact bytes
    at ``dest`` (the air-gapped / test-fixture path).

    The text pin asserts the branch *uses* ``cp``; this proves the copy
    actually delivers content — a regression that mangled the
    ``${url#file://}`` prefix strip (e.g. left ``file://`` on the path)
    would pass the text pin but fail to produce the file here.
    """
    src = tmp_path / "wheel.whl"
    src.write_bytes(b"canonical wheel payload")
    dest = tmp_path / "out" / "staged.download"
    dest.parent.mkdir()

    proc = _run_download_asset(tmp_path, f"file://{src}", dest)

    assert proc.returncode == 0, (
        f"download_asset on a file:// URL must succeed; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert dest.is_file(), (
        "download_asset(file://...) must create dest via cp — the "
        "air-gapped self-host path the old Python shutil.copyfile "
        f"supported. stderr={proc.stderr!r}"
    )
    assert dest.read_bytes() == b"canonical wheel payload", (
        "download_asset must copy the asset bytes verbatim; got "
        f"{dest.read_bytes()!r}"
    )


@pytest.mark.skipif(_BASH is None, reason="bash unavailable on this platform")
def test_download_asset_absolute_path_copies_payload(tmp_path: Path) -> None:
    """``download_asset`` on a bare absolute path (``/...``) must also
    copy locally rather than hand a filesystem path to curl.

    The ``/*`` case arm is the sibling of the ``file://`` arm; a manifest
    that lists a plain absolute ``tarball_url`` (offline mirror) must not
    be shelled out to curl, which would fail with ``Protocol ... not
    supported``.
    """
    src = tmp_path / "agent.tar.gz"
    src.write_bytes(b"tarball payload bytes")
    dest = tmp_path / "agent.download"

    proc = _run_download_asset(tmp_path, str(src), dest)

    assert proc.returncode == 0, (
        f"download_asset on an absolute path must succeed via cp; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert dest.read_bytes() == b"tarball payload bytes", (
        "download_asset must copy an absolute-path asset verbatim; got "
        f"{dest.read_bytes()!r}"
    )


@pytest.mark.skipif(_BASH is None, reason="bash unavailable on this platform")
def test_download_asset_missing_file_source_fails(tmp_path: Path) -> None:
    """A ``file://`` URL pointing at a non-existent source must make
    ``download_asset`` exit non-zero (``cp`` error under ``set -e``),
    not silently produce an empty dest.

    Guards the failure surface of the local-copy branch: a half-staged
    or zero-byte wheel that later trips pip with an opaque error is worse
    than a clean fail at download time.
    """
    missing = tmp_path / "does-not-exist.whl"
    dest = tmp_path / "out.download"

    proc = _run_download_asset(tmp_path, f"file://{missing}", dest)

    assert proc.returncode != 0, (
        "download_asset(file://<missing>) must fail loudly (cp errors "
        f"under set -e); stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_staging_python_renames_verifies_and_extracts(tmp_path: Path) -> None:
    """The staging Python must rename ``wheel.download`` to its canonical
    PEP 427 name, verify both sha256 digests, extract the tarball, and
    print the canonical wheel path.

    This is the end-to-end behaviour of the post-fix staging contract
    that no text pin can assert. ``download_asset`` stages the wheel
    under a temp ``wheel.download`` name; this step must rename it (pip
    25.2+ rejects a non-PEP-427 wheel filename) and emit the canonical
    path on stdout for ``stage_release`` to feed ``pip install``.
    """
    assets = tmp_path / "assets"
    assets.mkdir()
    tarball, wheel, wheel_name = _build_assets(assets, "9.9.9")
    manifest = _write_manifest(assets, tarball, wheel)

    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    # Reproduce what curl/download_asset stages: tarball under
    # agent.tar.gz, wheel under the temp wheel.download name.
    (stage_dir / "agent.tar.gz").write_bytes(tarball.read_bytes())
    (stage_dir / "wheel.download").write_bytes(wheel.read_bytes())

    proc = _run_staging(manifest, stage_dir, release_dir)

    assert proc.returncode == 0, (
        f"staging Python must succeed on valid assets; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    printed = proc.stdout.strip()
    assert printed.endswith(wheel_name), (
        "staging Python must print the canonical PEP 427 wheel path on "
        f"stdout (stage_release captures it for `pip install`); got "
        f"{printed!r}, expected to end with {wheel_name!r}"
    )
    assert (stage_dir / wheel_name).is_file(), (
        "staging Python must rename wheel.download to the canonical PEP "
        f"427 filename {wheel_name!r} so pip 25.2+ accepts it. "
        f"stderr={proc.stderr!r}"
    )
    assert not (stage_dir / "wheel.download").exists(), (
        "wheel.download must be renamed (moved), not left behind — the "
        "fix uses Path(...).replace(), an atomic rename."
    )
    assert (release_dir / "mobaide" / "__init__.py").is_file(), (
        "staging Python must extract the tarball into release_dir "
        f"(filter='data'). stderr={proc.stderr!r}"
    )


def test_staging_python_rejects_wheel_sha_mismatch(tmp_path: Path) -> None:
    """A wheel whose bytes don't match the manifest ``wheel_sha256`` must
    make the staging Python exit non-zero with a sha256 diagnostic.

    The integrity gate is the whole point of moving verification next to
    the (now curl-driven) download: a corrupted or substituted wheel must
    never reach ``pip install``. Pairs with the happy-path test to prove
    the verify step is load-bearing, not vacuous.
    """
    assets = tmp_path / "assets"
    assets.mkdir()
    tarball, wheel, _ = _build_assets(assets, "9.9.9")
    manifest = _write_manifest(assets, tarball, wheel, corrupt_wheel_sha=True)

    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (stage_dir / "agent.tar.gz").write_bytes(tarball.read_bytes())
    (stage_dir / "wheel.download").write_bytes(wheel.read_bytes())

    proc = _run_staging(manifest, stage_dir, release_dir)

    assert proc.returncode != 0, (
        "staging Python must reject a wheel whose sha256 differs from the "
        f"manifest; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "sha256 mismatch" in proc.stderr, (
        "staging Python's rejection must name the sha256 mismatch so the "
        f"operator can tell integrity failed; stderr={proc.stderr!r}"
    )
