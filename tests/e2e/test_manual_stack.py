"""Unit guards for ``tests/e2e/manual_stack.py`` (the manual-testing rollout).

These do NOT boot the stack: they exercise pure helpers, so they never request
the session-scoped ``e2e_stack`` fixture and run without server/agent
subprocesses. Run just this file with::

    python -m pytest tests/e2e/test_manual_stack.py -v
"""
from __future__ import annotations

import argparse
import json
import xml.dom.minidom as minidom
from pathlib import Path

import manual_stack as ms

PROD_URL = "https://mobaide.com"
TEST_URL = "http://192.168.1.5:9000"
LOOPBACK_URL = "http://127.0.0.1:9000"
GOOGLE_LINE = "GOOGLE_IOS_CLIENT_ID = 1234-abc.apps.googleusercontent.com"
SIM_A = "F0036AE7-EF09-4999-A3F0-B7220D960A8F"
SIM_B = "1A486351-84A4-403F-BF9E-F1ED12366F59"


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


def _point_client_files_at(monkeypatch, tmp_path: Path) -> None:
    """Redirect the module's checkout paths into a throwaway tree."""
    android = tmp_path / "android"
    nsc = (
        android / "app" / "src" / "debug" / "res" / "xml"
        / "network_security_config.xml"
    )
    ios = tmp_path / "ios"
    monkeypatch.setattr(ms, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ms, "ANDROID_DIR", android)
    monkeypatch.setattr(ms, "LOCAL_PROPERTIES_PATH", android / "local.properties")
    monkeypatch.setattr(ms, "DEBUG_NSC_PATH", nsc)
    monkeypatch.setattr(ms, "IOS_DIR", ios)
    monkeypatch.setattr(ms, "LOCAL_XCCONFIG_PATH", ios / "Config" / "Local.xcconfig")


def _central_url(managed) -> str | None:
    return managed.current_url(ms._read_config_lines(managed.path))


def _seed_local_properties(extra: str = "sdk.dir=/opt/android-sdk\n") -> None:
    ms.LOCAL_PROPERTIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ms.LOCAL_PROPERTIES_PATH.write_text(
        f"{extra}CENTRAL_SERVER_URL={PROD_URL}\n", encoding="utf-8"
    )


def test_stop_restores_previous_url_and_rebuild_hint_stays_quiet(monkeypatch, tmp_path):
    """up (sync) then stop (revert) restores the user's URL, and a re-up to the
    same URL does NOT re-fire the rebuild prompt (``baked_url`` tracking)."""
    _point_client_files_at(monkeypatch, tmp_path)
    _seed_local_properties()
    state = ms.default_state()
    noop = lambda: None  # noqa: E731

    # up #1: first build -> rebuild expected; the prod URL is remembered.
    assert ms.sync_android_files(state, TEST_URL, noop) is True
    assert _central_url(ms.ANDROID_URL_FILE) == TEST_URL
    assert state["android"]["previous_central_server_url"] == PROD_URL
    assert ms.DEBUG_NSC_PATH.exists()

    # stop: restore the checkout.
    ms.revert_android_files(state, noop)
    assert _central_url(ms.ANDROID_URL_FILE) == PROD_URL
    assert "sdk.dir=/opt/android-sdk" in ms.LOCAL_PROPERTIES_PATH.read_text()
    assert not ms.DEBUG_NSC_PATH.exists()

    # up #2 to the SAME URL: the installed app already has it -> no rebuild nag.
    assert ms.sync_android_files(state, TEST_URL, noop) is False
    assert state["android"]["previous_central_server_url"] == PROD_URL

    # stop again still restores the user's URL.
    ms.revert_android_files(state, noop)
    assert _central_url(ms.ANDROID_URL_FILE) == PROD_URL

    # a genuinely different URL still asks for a rebuild.
    assert ms.sync_android_files(state, "http://10.0.2.2:9001", noop) is True


def test_revert_leaves_foreign_url_alone(monkeypatch, tmp_path):
    """A CENTRAL_SERVER_URL the script never wrote (config profile / hand edit)
    must survive revert untouched."""
    _point_client_files_at(monkeypatch, tmp_path)
    ms.LOCAL_PROPERTIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ms.LOCAL_PROPERTIES_PATH.write_text(
        "CENTRAL_SERVER_URL=https://someone-elses.example\n", encoding="utf-8"
    )
    state = ms.default_state()  # managed_url is None => not ours to revert

    ms.revert_android_files(state, lambda: None)

    assert _central_url(ms.ANDROID_URL_FILE) == "https://someone-elses.example"


def test_xcconfig_value_escapes_the_comment_slashes(monkeypatch, tmp_path):
    """xcconfig treats ``//`` as a comment even inside a value.

    An unescaped ``http://host`` silently truncates to ``http:``, which the app
    then rejects in favour of the production URL. The written line must carry
    the ``/$()/`` escape and round-trip back to the plain URL.
    """
    assert ms._xcconfig_escape(LOOPBACK_URL) == "http:/$()/127.0.0.1:9000"
    assert ms._xcconfig_parse(ms._xcconfig_escape(LOOPBACK_URL)) == LOOPBACK_URL

    _point_client_files_at(monkeypatch, tmp_path)
    ms.sync_ios_files(ms.default_state(), LOOPBACK_URL, lambda: None)

    written = ms.LOCAL_XCCONFIG_PATH.read_text(encoding="utf-8").splitlines()
    value_lines = [ln for ln in written if ms._CENTRAL_URL_RE.match(ln)]
    assert value_lines == ["CENTRAL_SERVER_URL = http:/$()/127.0.0.1:9000"]
    assert "//" not in value_lines[0]  # an unescaped pair would comment it out


def test_ios_sync_then_revert_preserves_hand_written_settings(monkeypatch, tmp_path):
    """Local.xcconfig legitimately holds a developer's Google client ids, so the
    sync/revert pair must merge one line rather than own the whole file."""
    _point_client_files_at(monkeypatch, tmp_path)
    ms.LOCAL_XCCONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ms.LOCAL_XCCONFIG_PATH.write_text(
        f"{GOOGLE_LINE}\nCENTRAL_SERVER_URL = https:/$()/mobaide.com\n",
        encoding="utf-8",
    )
    state = ms.default_state()
    noop = lambda: None  # noqa: E731

    assert ms.sync_ios_files(state, LOOPBACK_URL, noop) is True
    assert _central_url(ms.IOS_URL_FILE) == LOOPBACK_URL
    assert state["ios"]["previous_central_server_url"] == PROD_URL

    ms.revert_ios_files(state, noop)
    assert _central_url(ms.IOS_URL_FILE) == PROD_URL
    assert GOOGLE_LINE in ms.LOCAL_XCCONFIG_PATH.read_text(encoding="utf-8")

    # Re-syncing the same URL must not re-fire the rebuild prompt.
    assert ms.sync_ios_files(state, LOOPBACK_URL, noop) is False
    assert ms.sync_ios_files(state, "http://127.0.0.1:9001", noop) is True


def test_ios_revert_removes_a_file_it_created_whole(monkeypatch, tmp_path):
    """With no pre-existing Local.xcconfig, stop must leave the checkout clean."""
    _point_client_files_at(monkeypatch, tmp_path)
    state = ms.default_state()
    noop = lambda: None  # noqa: E731

    ms.sync_ios_files(state, LOOPBACK_URL, noop)
    assert ms.LOCAL_XCCONFIG_PATH.exists()

    ms.revert_ios_files(state, noop)
    assert not ms.LOCAL_XCCONFIG_PATH.exists()


def test_ios_revert_leaves_foreign_url_alone(monkeypatch, tmp_path):
    _point_client_files_at(monkeypatch, tmp_path)
    ms.LOCAL_XCCONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ms.LOCAL_XCCONFIG_PATH.write_text(
        "CENTRAL_SERVER_URL = https:/$()/someone-elses.example\n", encoding="utf-8"
    )
    state = ms.default_state()  # managed_url is None => not ours to revert

    ms.revert_ios_files(state, lambda: None)

    assert ms.LOCAL_XCCONFIG_PATH.exists()
    assert _central_url(ms.IOS_URL_FILE) == "https://someone-elses.example"


def test_ios_auto_prefers_loopback_where_android_prefers_the_lan():
    """The deliberate divergence: iOS ships no ATS exception, so only loopback
    carries cleartext REST. Android's ``auto`` keeps favouring the LAN IP."""
    args = argparse.Namespace(
        host="0.0.0.0", port=9000, ios_url="auto", android_url="auto"
    )
    assert ms.resolve_ios_url(args, "192.168.1.5") == LOOPBACK_URL
    assert ms.resolve_android_url(args, "192.168.1.5") == TEST_URL

    args.ios_url = "lan"
    assert ms.resolve_ios_url(args, "192.168.1.5") == TEST_URL

    # A specific-interface bind is the only reachable address; loopback is not.
    args.host, args.ios_url = "192.168.1.5", "auto"
    assert ms.resolve_ios_url(args, "10.0.0.1") == TEST_URL


def test_ios_revert_restores_an_inline_comment_verbatim(monkeypatch, tmp_path):
    """``//`` opens a comment anywhere in an xcconfig, so a developer may
    annotate their own line. Re-rendering from the parsed URL would fold the
    note into the value and escape its delimiter, writing back a broken
    setting — the original line has to come back byte for byte.
    """
    _point_client_files_at(monkeypatch, tmp_path)
    original = "CENTRAL_SERVER_URL = https:/$()/prod.example  // the real one"
    ms.LOCAL_XCCONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ms.LOCAL_XCCONFIG_PATH.write_text(original + "\n", encoding="utf-8")
    state = ms.default_state()
    noop = lambda: None  # noqa: E731

    ms.sync_ios_files(state, LOOPBACK_URL, noop)
    # The comment must not leak into the remembered URL either.
    assert state["ios"]["previous_central_server_url"] == "https://prod.example"

    ms.revert_ios_files(state, noop)
    assert ms.LOCAL_XCCONFIG_PATH.read_text(encoding="utf-8").splitlines() == [original]


def test_ios_install_gate_tracks_the_installed_url_not_the_baked_one(monkeypatch, tmp_path):
    """`up` bakes a URL, then the banner tells you to run `ios --install-app`.

    That second run re-syncs the same URL, so the rebuild verdict is False —
    but the simulator still carries the previous build. The stale-install
    decision must therefore read ``installed_urls``, which only a successful
    install advances.
    """
    _point_client_files_at(monkeypatch, tmp_path)
    state = ms.default_state()
    noop = lambda: None  # noqa: E731
    ios = ms.IOS_URL_FILE.own_state(state)

    # `up` syncs a new URL: rebuild prompt fires, nothing is installed yet.
    assert ms.sync_ios_files(state, LOOPBACK_URL, noop) is True
    assert ios["baked_url"] == LOOPBACK_URL
    assert ios["installed_urls"] == {}

    # The documented follow-up re-syncs the same URL: no rebuild prompt...
    assert ms.sync_ios_files(state, LOOPBACK_URL, noop) is False
    # ...yet the install is still stale, so it must not be treated as current.
    assert ios["installed_urls"].get(SIM_A) != LOOPBACK_URL

    # After an install lands, the same URL is no longer stale on THAT device —
    # but a second simulator has its own container and keychain, so it is.
    ios["installed_urls"][SIM_A] = LOOPBACK_URL
    assert ios["installed_urls"].get(SIM_A) == LOOPBACK_URL
    assert ios["installed_urls"].get(SIM_B) != LOOPBACK_URL


def test_shutdown_save_keeps_a_concurrent_installers_record(tmp_path):
    """The banner tells the operator to run `ios --install-app` from another
    shell while `up` is still streaming logs. `up` holds the pre-install state
    in memory, so its shutdown save must not put that back — doing so costs the
    next install a needless device-wide keychain reset.
    """
    # What the concurrent `ios --install-app` process wrote.
    on_disk = ms.default_state()
    on_disk["ios"]["installed_urls"] = {SIM_A: LOOPBACK_URL}
    ms.save_state(tmp_path, on_disk)

    # What `up` still holds: it synced the URL but never installed anything.
    live = ms.default_state()
    live["ios"]["baked_url"] = LOOPBACK_URL

    ms.merge_external_install_state(tmp_path, live)
    ms.save_state(tmp_path, live)

    reloaded = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert reloaded["ios"]["installed_urls"] == {SIM_A: LOOPBACK_URL}
    assert reloaded["ios"]["baked_url"] == LOOPBACK_URL  # up's own writes survive
