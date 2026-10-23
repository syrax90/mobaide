"""Unit guards for ``tests/e2e/manual_stack.py`` (the manual-testing rollout).

These do NOT boot the stack: they exercise pure helpers, so they never request
the session-scoped ``e2e_stack`` fixture and run without server/agent
subprocesses. Run just this file with::

    python -m pytest tests/e2e/test_manual_stack.py -v
"""
from __future__ import annotations

import argparse
import json
import os
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


def test_web_install_reason_decision(tmp_path):
    """`up --web` re-runs `npm ci` exactly when the installed tree cannot be
    current: node_modules missing, or package-lock.json newer than the
    node_modules/.package-lock.json marker npm writes on every install. A tree
    with no lockfile at all must count as current, or the check would re-run
    `npm ci` on every boot forever."""
    assert ms.web_install_reason(tmp_path) is not None  # nothing installed

    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    lock = tmp_path / "package-lock.json"
    installed = node_modules / ".package-lock.json"
    lock.write_text("{}", encoding="utf-8")
    installed.write_text("{}", encoding="utf-8")

    os.utime(lock, (1_000, 1_000))
    os.utime(installed, (2_000, 2_000))
    assert ms.web_install_reason(tmp_path) is None  # install postdates lockfile

    os.utime(lock, (3_000, 3_000))
    assert ms.web_install_reason(tmp_path) is not None  # lockfile changed since

    installed.unlink()
    assert ms.web_install_reason(tmp_path) is not None  # tree of unknown vintage

    lock.unlink()
    assert ms.web_install_reason(tmp_path) is None  # no lockfile: never loop


def test_build_web_env_targets_the_stack_and_sanitizes(monkeypatch):
    """The vite proxy must follow the actual server bind (wildcard -> loopback)
    and the login pre-fill must carry this stack's account, while stray shell
    exports never leak into the web child: a leftover MOBAIDE_WEB_PROXY_TARGET
    would point the proxy at a dead stack, leftover MOBAIDE_WEB_DEV_LOGIN_* would
    pre-fill someone else's credentials, and NODE_ENV=production would make
    `vite dev` misbehave."""
    monkeypatch.setenv("MOBAIDE_WEB_PROXY_TARGET", "http://stale:1")
    monkeypatch.setenv("MOBAIDE_WEB_DEV_LOGIN_EMAIL", "stale@test.local")
    monkeypatch.setenv("MOBAIDE_WEB_DEV_LOGIN_PASSWORD", "stale-pass")
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("MOBAIDE_ENV", "test")
    state = {"email": "dev@test.local", "password": "Qwe>123"}

    env = ms.build_web_env(argparse.Namespace(host="0.0.0.0", port=9123), state)
    assert env["MOBAIDE_WEB_PROXY_TARGET"] == "http://127.0.0.1:9123"
    assert env["MOBAIDE_WEB_DEV_LOGIN_EMAIL"] == "dev@test.local"
    assert env["MOBAIDE_WEB_DEV_LOGIN_PASSWORD"] == "Qwe>123"
    assert "NODE_ENV" not in env
    assert "MOBAIDE_ENV" not in env

    env = ms.build_web_env(argparse.Namespace(host="192.168.1.5", port=9123), state)
    assert env["MOBAIDE_WEB_PROXY_TARGET"] == "http://192.168.1.5:9123"


def test_up_parser_web_defaults():
    """--web is per-run opt-in: plain `up` must not grow web behaviour, and
    5173 stays the default origin (the one registered for OAuth in dev)."""
    args = ms.build_parser().parse_args(["up"])
    assert args.web is False
    assert args.web_port == ms.DEFAULT_WEB_PORT == 5173
    assert args.no_browser is False  # --web opens a tab unless asked not to

    args = ms.build_parser().parse_args(
        ["up", "--web", "--web-port", "5180", "--no-browser"]
    )
    assert args.web is True
    assert args.web_port == 5180
    assert args.no_browser is True


def test_open_web_client_is_best_effort(monkeypatch):
    """The tab is a convenience layered on a stack that is already READY, so it
    targets the pre-filled /login screen and a browser-less box (SSH, CI, a
    stripped container) must not turn a working stack into a failed boot."""
    opened = []
    monkeypatch.setattr(ms.webbrowser, "open_new_tab", lambda url: opened.append(url) or True)
    ms.open_web_client(5180)
    assert opened == ["http://localhost:5180/login"]

    def explode(url):
        raise RuntimeError("no browser here")

    monkeypatch.setattr(ms.webbrowser, "open_new_tab", explode)
    ms.open_web_client(5180)  # must return normally


def test_stale_matcher_covers_the_web_child():
    """A hard-killed `up --web` leaves an npm/node tree behind; the reaping
    pre-filter must recognise it on every platform (npm/node on POSIX, cmd.exe
    on Windows) while still rejecting a python process wearing a recycled pid."""
    matchers = {name: (token, names) for name, token, names in ms.STALE_PROCESS_MATCHERS}
    assert set(matchers) == {"server", "agent", "web"}

    cmdline_token, name_tokens = matchers["web"]
    assert cmdline_token == "npm"
    for proc_name in ("node", "npm", "npm.cmd", "cmd.exe"):
        assert any(t in proc_name.lower() for t in name_tokens), proc_name
    assert not any(t in "python3.12" for t in name_tokens)


def test_up_parser_emulator_is_opt_in():
    """Plain `up` (the macOS workflow) must not boot anything new; a bare
    --emulator picks the shared AVD name, and an explicit one wins."""
    args = ms.build_parser().parse_args(["up"])
    assert args.emulator is None
    assert args.keep_emulator is False

    args = ms.build_parser().parse_args(["up", "--emulator", "--web"])
    assert args.emulator == ms.DEFAULT_AVD == "Mobaide-E2E"
    assert args.web is True

    args = ms.build_parser().parse_args(["android", "--emulator", "Pixel_9", "--install-app"])
    assert args.emulator == "Pixel_9"

    args = ms.build_parser().parse_args(["stop"])
    assert args.timeout == ms.DEFAULT_STOP_TIMEOUT


def test_parse_adb_devices_skips_daemon_chatter():
    output = (
        "* daemon not running; starting now at tcp:5037\n"
        "* daemon started successfully\n"
        "List of devices attached\n"
        "emulator-5554\tdevice\n"
        "emulator-5556\toffline\n"
        "R58N12345\tunauthorized\n"
        "\n"
    )
    assert ms.parse_adb_devices(output) == {
        "emulator-5554": "device",
        "emulator-5556": "offline",
        "R58N12345": "unauthorized",
    }


def test_parse_avd_list_ignores_log_lines():
    output = (
        "INFO    | Storing crashdata in: C:\\tmp\\crashdata\n"
        "Mobaide-E2E\n"
        "Pixel_9_API_36\n"
    )
    assert ms.parse_avd_list(output) == ["Mobaide-E2E", "Pixel_9_API_36"]


def _fake_image(sdk: Path, api: str, tag: str, abi: str) -> None:
    image = sdk / "system-images" / api / tag / abi
    image.mkdir(parents=True)
    (image / "source.properties").write_text("Pkg.Revision=1\n", encoding="utf-8")


def test_pick_system_image_prefers_stable_4k_newest(tmp_path):
    """The layout found on a real Windows box: the newest image is a 16 KB-page
    one and a CANARY preview exists -- neither may win over a stable 4 KB one."""
    _fake_image(tmp_path, "android-36.1", "google_apis_playstore", "x86_64")
    _fake_image(tmp_path, "android-36.1", "google_apis_playstore_ps16k", "x86_64")
    _fake_image(tmp_path, "android-37.0", "google_apis_playstore_ps16k", "x86_64")
    _fake_image(tmp_path, "android-CANARY", "google_apis_playstore", "x86_64")
    _fake_image(tmp_path, "android-35", "google_apis", "x86_64")
    _fake_image(tmp_path, "android-36", "android-tv", "x86_64")
    (tmp_path / "system-images" / "android-38" / "google_apis" / "x86_64").mkdir(
        parents=True)  # half-downloaded: no source.properties

    assert ms.pick_system_image(tmp_path, "AMD64") == (
        "system-images;android-36.1;google_apis_playstore;x86_64")
    assert ms.pick_system_image(tmp_path, "arm64") is None  # wrong ABI only


def test_pick_system_image_falls_back_to_16k(tmp_path):
    _fake_image(tmp_path, "android-37.0", "google_apis_playstore_ps16k", "arm64-v8a")
    assert ms.pick_system_image(tmp_path, "arm64") == (
        "system-images;android-37.0;google_apis_playstore_ps16k;arm64-v8a")


def test_enable_hardware_keyboard_is_idempotent(tmp_path):
    config = tmp_path / "config.ini"
    config.write_text("hw.keyboard=no\nhw.lcd.density=420\n", encoding="utf-8")
    ms.enable_hardware_keyboard(config)
    ms.enable_hardware_keyboard(config)
    lines = config.read_text(encoding="utf-8").splitlines()
    assert lines.count("hw.keyboard=yes") == 1
    assert "hw.keyboard=no" not in lines
    assert "hw.lcd.density=420" in lines
    ms.enable_hardware_keyboard(tmp_path / "missing.ini")  # must not raise


def test_stop_without_a_running_stack_is_a_noop(tmp_path, capsys):
    """`stop` after a clean shutdown (runtime cleared) must not revert files a
    --keep-android run deliberately left in place."""
    (tmp_path / "state.json").write_text(
        json.dumps({**ms.default_state(), "runtime": {}}), encoding="utf-8")
    args = ms.build_parser().parse_args(["stop", "--state-dir", str(tmp_path)])
    args.state_dir = tmp_path
    assert ms.cmd_stop(args) == 0
    assert "nothing to stop" in capsys.readouterr().out


def test_teardown_leftovers_honors_the_keep_flags(monkeypatch, tmp_path):
    """A hard-killed run's cleanup restores only what that run synced and did
    not opt to keep, exactly as the run's own teardown would have."""
    _point_client_files_at(monkeypatch, tmp_path)
    _seed_local_properties()
    state = ms.default_state()
    ms.sync_android_files(state, TEST_URL, lambda: None)

    import psutil

    state["runtime"] = {"synced": ["android"], "keep": ["android"]}
    ms.teardown_leftovers(tmp_path / "state", state, psutil)
    assert _central_url(ms.ANDROID_URL_FILE) == TEST_URL
    assert state["runtime"] == {}

    # A crashed `up --no-android` never synced Android: an override an
    # earlier run left in place must survive its cleanup.
    state["runtime"] = {"synced": [], "keep": []}
    ms.teardown_leftovers(tmp_path / "state", state, psutil)
    assert _central_url(ms.ANDROID_URL_FILE) == TEST_URL

    state["runtime"] = {"synced": ["android"], "keep": []}
    ms.teardown_leftovers(tmp_path / "state", state, psutil)
    assert _central_url(ms.ANDROID_URL_FILE) == PROD_URL
    assert not ms.DEBUG_NSC_PATH.exists()


def test_recorded_live_process_requires_matching_create_time():
    """The ownership gate `stop` and the emulator teardown rely on: a live pid
    whose create time differs (a recycled pid) is never treated as ours."""
    import psutil

    me = psutil.Process()
    runtime = {"up_pid": me.pid, "up_create_time": me.create_time()}
    assert ms.recorded_live_process(runtime, "up", psutil).pid == me.pid
    runtime["up_create_time"] -= 3600
    assert ms.recorded_live_process(runtime, "up", psutil) is None
    assert ms.recorded_live_process({}, "up", psutil) is None


def test_failed_emulator_never_falls_back_to_an_unpinned_install(monkeypatch):
    """An unpinned installDebug hits every attached device, so a requested but
    unavailable emulator must skip the install rather than overwrite the app
    on a connected phone; without --emulator the old behavior stays."""
    calls = []
    monkeypatch.setattr(ms, "run_gradle_install",
                        lambda *a, **kw: calls.append((a, kw)) or True)

    assert ms.install_on_emulator_or_device(
        None, Path("emu.log"), emulator_requested=True) is False
    assert calls == []

    assert ms.install_on_emulator_or_device(
        None, Path("emu.log"), emulator_requested=False) is True
    assert calls == [((), {})]


def test_kill_tree_spares_the_given_subtree():
    """A forced `stop` kills the `up` tree, but the detached emulator is still
    its descendant; the spared subtree must survive so --keep-emulator and the
    clean `adb emu kill` shutdown are still decided by teardown_leftovers."""
    import subprocess
    import sys

    import psutil

    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    code = (
        "import subprocess, sys, time;"
        f"a = subprocess.Popen({sleeper!r});"
        f"b = subprocess.Popen({sleeper!r});"
        "print(a.pid, b.pid, flush=True); time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, text=True)
    spared_pid = None
    try:
        spared_pid, doomed_pid = map(int, parent.stdout.readline().split())
        ms.kill_tree(psutil, parent.pid, spare=psutil.Process(spared_pid))
        assert parent.wait(timeout=10) is not None
        assert not psutil.pid_exists(doomed_pid) or (
            psutil.Process(doomed_pid).status() == psutil.STATUS_ZOMBIE)
        assert psutil.Process(spared_pid).is_running()
    finally:
        ms.kill_tree(psutil, spared_pid)
        ms.kill_tree(psutil, parent.pid)
