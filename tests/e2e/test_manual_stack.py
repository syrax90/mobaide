"""Unit guards for ``tests/e2e/manual_stack.py`` (the manual-testing rollout).

These do NOT boot the stack: they exercise pure helpers, so they never request
the session-scoped ``e2e_stack`` fixture and run without server/agent
subprocesses. Run just this file with::

    python -m pytest tests/e2e/test_manual_stack.py -v
"""
from __future__ import annotations

import xml.dom.minidom as minidom
from pathlib import Path

import manual_stack as ms

PROD_URL = "https://mobaide.com"
TEST_URL = "http://192.168.1.5:9000"


def test_debug_nsc_content_is_valid_xml():
    """The generated debug network-security-config must parse as XML.

    Regression guard: an XML comment may not contain the literal ``--`` (it is
    reserved for the ``-->`` terminator), which expat here — and Android's
    resource parser — reject. A CLI flag such as ``--revert`` left in the header
    comment silently broke ``gradlew :app:installDebug``.
    """
    minidom.parseString(ms.DEBUG_NSC_CONTENT)  # raises on malformed XML
    assert 'cleartextTrafficPermitted="true"' in ms.DEBUG_NSC_CONTENT
    assert ms.NSC_MARKER in ms.DEBUG_NSC_CONTENT


def _point_android_files_at(monkeypatch, tmp_path: Path) -> None:
    """Redirect the module's checkout paths into a throwaway tree."""
    android = tmp_path / "android"
    nsc = (
        android / "app" / "src" / "debug" / "res" / "xml"
        / "network_security_config.xml"
    )
    monkeypatch.setattr(ms, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ms, "ANDROID_DIR", android)
    monkeypatch.setattr(ms, "LOCAL_PROPERTIES_PATH", android / "local.properties")
    monkeypatch.setattr(ms, "DEBUG_NSC_PATH", nsc)


def _central_url() -> str | None:
    return ms._current_central_url(ms._read_local_properties_lines())


def _seed_local_properties(extra: str = "sdk.dir=/opt/android-sdk\n") -> None:
    ms.LOCAL_PROPERTIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ms.LOCAL_PROPERTIES_PATH.write_text(
        f"{extra}CENTRAL_SERVER_URL={PROD_URL}\n", encoding="utf-8"
    )


def test_stop_restores_previous_url_and_rebuild_hint_stays_quiet(monkeypatch, tmp_path):
    """up (sync) then stop (revert) restores the user's URL, and a re-up to the
    same URL does NOT re-fire the rebuild prompt (``baked_url`` tracking)."""
    _point_android_files_at(monkeypatch, tmp_path)
    _seed_local_properties()
    state = ms.default_state()
    noop = lambda: None  # noqa: E731

    # up #1: first build -> rebuild expected; the prod URL is remembered.
    assert ms.sync_android_files(state, TEST_URL, noop) is True
    assert _central_url() == TEST_URL
    assert state["android"]["previous_central_server_url"] == PROD_URL
    assert ms.DEBUG_NSC_PATH.exists()

    # stop: restore the checkout.
    ms.revert_android_files(state, noop)
    assert _central_url() == PROD_URL
    assert "sdk.dir=/opt/android-sdk" in ms.LOCAL_PROPERTIES_PATH.read_text()
    assert not ms.DEBUG_NSC_PATH.exists()

    # up #2 to the SAME URL: the installed app already has it -> no rebuild nag.
    assert ms.sync_android_files(state, TEST_URL, noop) is False
    assert state["android"]["previous_central_server_url"] == PROD_URL

    # stop again still restores the user's URL.
    ms.revert_android_files(state, noop)
    assert _central_url() == PROD_URL

    # a genuinely different URL still asks for a rebuild.
    assert ms.sync_android_files(state, "http://10.0.2.2:9001", noop) is True


def test_revert_leaves_foreign_url_alone(monkeypatch, tmp_path):
    """A CENTRAL_SERVER_URL the script never wrote (config profile / hand edit)
    must survive revert untouched."""
    _point_android_files_at(monkeypatch, tmp_path)
    ms.LOCAL_PROPERTIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ms.LOCAL_PROPERTIES_PATH.write_text(
        "CENTRAL_SERVER_URL=https://someone-elses.example\n", encoding="utf-8"
    )
    state = ms.default_state()  # managed_url is None => not ours to revert

    ms.revert_android_files(state, lambda: None)

    assert _central_url() == "https://someone-elses.example"
