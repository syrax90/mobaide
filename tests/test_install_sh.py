"""Static pin on ``deploy/install.sh`` for the macOS SSL fix.

A python.org framework build can ship an empty OpenSSL CA store, so any
``urllib`` download in the installer failed with
``CERTIFICATE_VERIFY_FAILED``. Downloads go through curl
(``download_asset``); the staging Python must stay network-free.
"""
from __future__ import annotations

from test_install_script_trap import _sh_function_body


def test_stage_release_staging_python_does_no_urllib_network() -> None:
    body = _sh_function_body("stage_release")
    assert "urllib.request" not in body
    assert "urlopen" not in body
