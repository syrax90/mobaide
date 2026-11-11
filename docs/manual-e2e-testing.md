# Manual E2E testing (real phone / emulator / simulator against a local stack)

`tests/e2e/manual_stack.py` boots the **real** central_server + **real** mobaide agent on your PC and wires the **real Android app** — and, on macOS, the **real iOS app** on the simulator — to them. A human tester can then exercise the full mobile ↔ server ↔ agent path hands-on: registration, login, agent list, SHELL terminals and managed claude/codex/antigravity sessions.

With `up --web` it also serves the **web client** (`web/`) on `http://localhost:5173`, proxied to the same stack, and opens it in a browser tab with the throwaway account filled into the login form.

It is the manual-testing counterpart of the automated pytest harness in [e2e-harness.md](e2e-harness.md) and reuses its recipe outside pytest.

One command, cross-platform (Windows / macOS / Linux), repeatable, and safe: **no tracked file is ever modified and release builds are unaffected** (see [Production safety](#production-safety)).

**On this page**

- [Quickstart](#quickstart)
- [What the script does](#what-the-script-does)
- [What the script writes into your checkout](#what-the-script-writes-into-your-checkout)
- [Connectivity modes](#connectivity-modes)
- [Day-2 commands](#day-2-commands)
- [Troubleshooting](#troubleshooting)
- [Production safety](#production-safety)

## Quickstart

```
# 1. activate a Python 3.12+ env (example)          conda activate mobaide-e2e-tests
# 2. from the repo root:
python tests/e2e/manual_stack.py up
```

On the first run the script pip-installs `mobaide_agent[dev]` and
`central_server[dev]` editable into the active interpreter if they are missing.
When the READY banner appears:

```
# 3a. Android: build + install the DEBUG app on a connected device
cd android
.\gradlew.bat :app:installDebug        # Windows
./gradlew :app:installDebug            # macOS / Linux

# 3b. iOS (macOS): boot a simulator, then let the script build + install
xcrun simctl boot 'iPhone 16'
python tests/e2e/manual_stack.py ios --install-app

# 3c. web (optional): start with `up --web` instead -- a browser tab opens on
#     http://localhost:5173/login with the banner credentials pre-filled;
#     press Sign In yourself (--no-browser opts out of the tab)
#     (the first --web run auto-installs web/node_modules via `npm ci`)
```

On Windows (no iOS) or any machine with the Android SDK, the whole Android +
web loop is one command — the script boots the emulator, builds, installs and
launches the app, and serves the web client:

```
python tests/e2e/manual_stack.py up --host 127.0.0.1 --android-url emulator --emulator --install-app --web
# later, from any other shell (the only graceful stop on Windows):
python tests/e2e/manual_stack.py stop
```

Then open the app on the phone or simulator, **register or log in** with the credentials from the banner (email `dev@test.local`, password `Qwe>1234` — both fixed, easy dev-testing defaults; override with `--email`/`--password`), pick the `manual-e2e-agent`, and start testing.

`Ctrl+C` stops the stack and **restores `android/local.properties` and `ios/Config/Local.xcconfig`** to their previous `CENTRAL_SERVER_URL` (e.g. `https://mobaide.com`), so the checkout is left as it was found.

State persists, so the next `up` reuses the same account, agent, and database — the phone stays logged in. Pass `--keep-android` / `--keep-ios` to leave the test URL in place across restarts instead.

## What the script does

1. Ensures both packages are installed editable (Python 3.12+).
2. Boots `python -m central_server` bound to `0.0.0.0:9000` with `MOBAIDE_ENV=test`: throwaway SQLite DB, in-process email sink (no SMTP), `EMAIL_VERIFICATION_REQUIRED=false`.
3. Registers (or logs back into) the tester account and creates (or reuses) the agent over the real REST flow, exactly like the Android app would.
4. Boots `python -m mobaide` in **production mode** — a **real PTY**, so SHELL sessions run a real shell and claude/codex/antigravity sessions run the real CLIs.
   - It is pointed at the local relay over `ws://127.0.0.1:9000` (`MOBAIDE_AGENT_ALLOW_INSECURE_WS=1`).
   - All agent state is isolated under a throwaway `MOBAIDE_AGENT_PREFIX`.
5. Gates readiness on `/api/health` → `agents_online >= 1` (the same 4012-safe gate the automated harness uses).
   - Those counts are admin-gated, so the server is also booted with an internal, script-only admin account (`ADMIN_SEED_EMAIL`/`ADMIN_SEED_PASSWORD`, distinct from your tester login) whose token the script uses to poll them. `status` does the same, falling back to bare status if that login fails.
6. Syncs the **gitignored** client overrides (next section) and prints the READY banner, then streams `[server]` / `[agent]` logs until `Ctrl+C` (logs are also written under the state dir).
   - iOS is synced only on macOS, where the app can actually be built; `--ios` forces it elsewhere.
7. With `--web`, boots `npm run dev` in `web/` on port 5173 (`--web-port` to change) with the Vite proxy pointed at the actual `--host`/`--port` via `MOBAIDE_WEB_PROXY_TARGET`, auto-running `npm ci` first when `web/node_modules` is missing or older than the lockfile.
   - Once the stack is READY it opens `http://localhost:5173/login` in a new browser tab (`--no-browser` to skip, e.g. over SSH) with the account pre-filled. Nothing is submitted; you press Sign In.
   - The credentials travel to the login form's `defaultValues` as `MOBAIDE_WEB_DEV_LOGIN_EMAIL`/`_PASSWORD`, which `web/vite.config.ts` injects **only** under `vite dev` -- `vite build` always bakes `null`, so no production bundle can carry them.
   - The web child is supervised, logged (`[web]`, `<state>/logs/web.log`), stopped and crash-reaped like server and agent. It writes nothing to the checkout except the gitignored `web/node_modules` (kept across runs).
8. With `--emulator [AVD]` (default AVD `Mobaide-E2E`), reuses that AVD if it is already running, otherwise boots it (detached, log in `<state>/logs/emulator.log`).
   - A missing AVD is created once with `avdmanager` from the best installed phone system image for the host CPU, on the `pixel_7` profile with the host keyboard enabled.
   - With `--install-app` the APK is assembled *while* the emulator boots, installed with `ANDROID_SERIAL` pinned to it, and launched once the stack is READY.
   - The SDK is found via `ANDROID_HOME` / `ANDROID_SDK_ROOT`, else Android Studio's per-OS default location.
9. On stop, restores both `CENTRAL_SERVER_URL` values to what they were, removes the generated Android debug override (skip with `--keep-android` / `--keep-ios`), and shuts down an emulator it booted with `adb emu kill`.
   - `adb emu kill` saves the quick-boot snapshot, so the next boot takes seconds. `--keep-emulator` leaves it running; an emulator it only reused is never touched.

### Stopping, and why Windows needs more than Ctrl+C

`Ctrl+C` in the `up` console and `kill -TERM <pid>` on macOS/Linux both run the normal teardown.

`python tests/e2e/manual_stack.py stop` does the same from any other shell on every OS. It drops a `stop.request` file into the state dir, which `up` polls once a second, and waits (`--timeout`, default 90 s) for that `up` to exit.

- Windows has no `SIGTERM` a second shell could send, so `stop` is the way to end a detached or background run there.
- If the run does not exit in time it is killed.
- If it was already gone (hard-killed), `stop` reaps what it left behind — children, an emulator it booted, the client files — honouring the `--keep-*` flags that run was started with.

**Windows job object.** On Windows every child (server, agent, Vite) is assigned to a kill-on-close **job object** owned by `up`. A `TerminateProcess` (Task Manager, `Stop-Process`, a closed terminal) skips every `finally` block, but the kernel still kills the whole child tree, PTY shells included, so no orphan keeps ports 9000/5173 busy. The client files of such a run are restored by the next `stop`, `clean`, or `up`.

**State directory.** State lives in `<system-temp>/mobaide-manual-e2e` by default (`--state-dir` to move it):

- `state.json` (JWT secret, account, agent id + token, last-used port),
- `server/` (DB + manifest),
- `agent/` (isolated agent prefix),
- `logs/`.

An explicit `--port` is remembered; bare re-runs reuse it.

### Why the agent runs in production mode

`MOBAIDE_ENV=test` on the **agent** selects the fake echo PTY backend — no real shells, no real CLIs — which suits the automated harness but not manual testing. The script therefore runs the agent in production mode by default. If you *want* the dependency-free echo mode (e.g. to verify the infrastructure on a machine with no CLIs), use `--fake-pty`, and `--fake-pty --smoke` to also drive one echo session end-to-end.

Chat sessions follow the same switch. In production mode they run the real ACP adapters (Node.js on the PC and a signed-in `claude` or `codex`; for Antigravity, a signed-in `agy` that supports `agy acp`). `MOBAIDE_ACP_CODEX_COMMAND` and `MOBAIDE_ACP_ANTIGRAVITY_COMMAND` override the Codex adapter and the `agy acp` command. With `--fake-pty` they run the scripted fake ACP agent (`mobaide_agent/mobaide/acp/fake_agent.py`), so every chat state is reachable without an account: the message text picks the scenario — `tools`, `permission`, `question`, `plan`, `markdown`, `slow` (also use it to try Send now), `external` (a message from another app), `fail`, `crash`, anything else echoes. To try the sign-in card, create the marker file named by `MOBAIDE_FAKE_ACP_SIGNED_OUT`: the fake agent then answers authRequired. `fake_agent.py login` is the scripted login command, and it clears the marker. Codex sessions use `--flavor codex` of the fake agent, and Antigravity sessions use `--flavor antigravity`. Continue a session lists two fake stored sessions.

This is safe on a machine with a production agent install: the auto-updater is fail-closed (no trust key in the throwaway prefix), and the explicit child environment + throwaway `MOBAIDE_AGENT_PREFIX` keep the test agent away from any real install's `.env`, stores, and state.

## What the script writes into your checkout

Only three files, **all gitignored** — `git status` stays clean and there is
nothing to revert before committing:

| File | Content | Why it is safe |
|---|---|---|
| `android/local.properties` | `CENTRAL_SERVER_URL=http://<your-ip>:9000` merged in; every other line (e.g. `sdk.dir`) is preserved | gitignored; read only on developer machines (release CI passes the env var instead) |
| `android/app/src/debug/res/xml/network_security_config.xml` | generated debug-only override permitting cleartext HTTP | gitignored; the **debug source set is never merged into release builds**, and the tracked main config (cleartext disabled) is untouched |
| `ios/Config/Local.xcconfig` | `CENTRAL_SERVER_URL = http:/$()/127.0.0.1:9000` merged in; your `GOOGLE_*` overrides are preserved | gitignored; `#include?`-d by the tracked `Protocol.xcconfig`, which is never written (`protocol-integrity.yml` parses it) |

The server URL is **baked into the app at build time** on both platforms (`android/app/build.gradle.kts` for the APK; on iOS the xcconfig → `$(CENTRAL_SERVER_URL)` → `Info.plist` `CentralServerURL` chain that `AppConfig.swift` reads). Android additionally blocks cleartext HTTP unless the network security config allows it, which is what the second file is for.

After any URL change (new Wi-Fi lease, different `--port`), **rebuild + reinstall the app**; the banner warns when that is needed.

Two iOS-specific notes:

* The `/$()/` in the xcconfig value is **required**, not a typo.
  * xcconfig treats `//` as a comment even inside a value, so a raw `http://host` would be truncated to `http:`. `$()` is an empty build-setting reference that Xcode expands away, so the app sees a plain URL.
* iOS persists the relay base URL in the **keychain** at sign-in (`TokenStore` and `ConnectionStore` both do), so an upgrade install keeps opening agent sockets against the old stack even after the rebuild.
  * **`simctl uninstall` does not clear those items.** Only `xcrun simctl keychain <udid> reset` does, and simctl scopes that per device, never per app.
  * So `--install-app` uninstalls **and** resets that simulator's keychain — which also drops any `simctl keychain add-root-cert` anchors on it. Use `--ios-keep-keychain` to skip the reset and sign out in-app instead.
  * The "what is installed where" record is kept **per simulator UDID**, so a different device gets the cleanup again, and a failed reset leaves the device marked stale so the next run retries.

**All are undone automatically when the stack stops.** The previous `CENTRAL_SERVER_URL` values are put back and the debug override removed, so the checkout returns to its pre-test state with nothing to revert by hand.

- A file the script *created* is deleted whole; one it merged into keeps every other line.
- A `CENTRAL_SERVER_URL` the script did not write (configuration profile, hand edit) is never touched.
- The rebuild prompt tracks the URL the app was last built with, so restoring on stop does **not** make the next `up` nag for an unneeded rebuild.

To undo manually at any other time (e.g. after `--keep-*` or a hard kill that skipped teardown):

```
python tests/e2e/manual_stack.py android --revert
python tests/e2e/manual_stack.py ios --revert
```

## Connectivity modes

| Mode | Command | App URL | Notes |
|---|---|---|---|
| **Android LAN (default)** | `up` | `http://<PC-LAN-IP>:9000` | Phone and PC on the same Wi-Fi/subnet; allow the Windows firewall prompt (Private networks) |
| **Android emulator** | `up --android-url emulator` | `http://10.0.2.2:9000` | `10.0.2.2` is the emulator's alias for the host; add `--host 127.0.0.1` for a loopback-only run with no firewall prompt |
| **Android USB / adb reverse** | `up --android-url reverse`, then `adb reverse tcp:9000 tcp:9000` | `http://127.0.0.1:9000` | No LAN or firewall involvement; re-run `adb reverse` after replugging |
| **iOS simulator (default)** | `up` | `http://127.0.0.1:9000` | The simulator shares the Mac's network stack, so loopback just works — and it is the only cleartext host iOS allows without an ATS exception |
| **iOS, other host** | `up --ios-url lan` or `--ios-url http://…` | as given | Accepted, but see the ATS warning below |

`--advertise-ip <ip>` overrides LAN-IP auto-detection (useful with VPN/Hyper-V/
WSL adapters); the banner lists all candidate IPs to try.

### Why iOS defaults to loopback and a real iPhone does not work

`--ios-url auto` resolves to `127.0.0.1` whenever the server is reachable on
loopback, where Android's `auto` prefers the LAN IP. That is deliberate: the
tracked `ios/Mobaide/Support/Info.plist` carries **no `NSAppTransportSecurity`
exception in any configuration**, and App Transport Security blocks cleartext
`http://` to anything but loopback.

The failure is asymmetric and confusing, which is why the script warns loudly about it:

- REST goes through `URLSession` and fails with `-1022`, so sign-in breaks.
- WebSockets go through Network.framework, which ATS does not police, so terminals look perfectly healthy.

**Testing against a real iPhone over Wi-Fi therefore needs an ATS exception added to that tracked plist by hand**; the script deliberately does not add one, because it would ship in release builds. Use the simulator over loopback instead.

## Day-2 commands

| Command | Effect |
|---|---|
| `up` | boot; reuses persisted account/agent/DB (phone stays logged in) |
| `up --fresh` | wipe state first: new secret, account, agent, empty DB |
| `up --fake-pty [--smoke]` | echo-backend infra check (no shells/CLIs needed) |
| `up --fake-voice` | voice input against a tiny generated model with deterministic `fake transcript <n> ms` results (`MOBAIDE_VOICE_FAKE=1`), so the enable → dictate flows need no 670 MB download |
| `up --install-app` | build + install the app for every client that was synced |
| `android --install-app` | also run `gradlew :app:installDebug` |
| `ios --install-app` | also run `xcodebuild` + `xcrun simctl install`/`launch` against the **booted** simulator (uninstalling first when the URL changed) |
| `up --web` | also serve the web client at `http://localhost:5173`, proxied to this stack, and open it with the login pre-filled (`--web-port <n>` changes the port, `--no-browser` skips the tab) |
| `up --emulator [AVD]` | boot (or reuse) an Android emulator, default AVD `Mobaide-E2E`, created once if missing; shut down on stop when this run booted it (`--keep-emulator` leaves it running) |
| `android --emulator --install-app` | boot/reuse the emulator, install the debug app on it and launch it (the emulator stays up; this command has no teardown) |
| `stop` | gracefully stop the running `up` from another shell (any OS; the only graceful stop on Windows); reaps a hard-killed run's leftovers |
| `up --projects <paths>` | seed the agent's project list with real directories |
| `up --keep-android` / `--keep-ios` | on stop, leave the test URL in place (default: restore it) |
| `up --no-android` / `--no-ios` | skip that client's file sync entirely |
| `up --ios` | sync the iOS override off macOS too (default: macOS only) |
| `--ios-keep-keychain` | with `--install-app`, skip the keychain reset on a URL change (leaves the old relay URL behind) |
| `android` / `ios` | (re)sync that client's files without booting the stack |
| `android --revert` / `ios --revert` | manually undo now (also runs automatically on every `up` stop) |
| `status` | probe `/api/health`, show persisted identity |
| `clean` | kill stale processes, revert both clients' files, delete the state dir |

**CLI sessions.**

- SHELL sessions need nothing extra.
- claude / codex / antigravity sessions need that CLI installed on the PC (on `PATH`, or via `MOBAIDE_CLAUDE_BIN` / `MOBAIDE_CODEX_BIN` / `MOBAIDE_AGY_BIN`, which the script deliberately inherits from your shell).
- A missing CLI fails only at session-create time with a `<cli>_cli_missing` marker.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `port 9000 is busy` | A previous crashed run (`manual_stack.py clean` reaps it), the root `docker-compose.yml` dev server, or another app. Or use `--port`. |
| Firewall prompt denied / never appeared | Windows Settings → Firewall → Allow an app → allow Python on **Private** networks; or avoid the firewall entirely with the emulator or adb-reverse mode. |
| Phone cannot connect | Open `http://<PC-IP>:9000/api/health` in the phone's browser. If it fails: same Wi-Fi? AP/client isolation? VPN? Windows network profile set to Public? Wrong NIC picked → `--advertise-ip`. After a new DHCP lease, re-run `up` and rebuild + reinstall the app. |
| App shows network/cleartext errors | The installed build predates the file sync or is a RELEASE build. Re-run `python tests/e2e/manual_stack.py android`, then rebuild + reinstall the **debug** app. |
| iOS app still hits `api.mobaide.com` | No `ios/Config/Local.xcconfig`, or the build predates it. The URL is baked in at build time — re-run `ios`, then rebuild. Check the built app with `plutil -extract CentralServerURL raw <app>/Info.plist`. |
| iOS: terminals work but sign-in fails | ATS is blocking cleartext REST to a non-loopback host (`-1022`). Use `--ios-url simulator`, or add an `NSAppTransportSecurity` exception to the tracked `Info.plist` by hand. |
| iOS: agent offline / wrong server after a URL change | The relay URL is persisted in the keychain at sign-in, and an uninstall does **not** clear it. `ios --install-app` handles it (uninstall + `simctl keychain reset`); by hand, sign out in-app or `xcrun simctl keychain <udid> reset`. |
| `no booted iOS simulator` | `xcrun simctl boot 'iPhone 16'` (or open Simulator.app) and re-run `ios --install-app`. |
| `could not log in ... state.json no longer match` | DB and state diverged (e.g. deleted one of them). `up --fresh`, or pass matching `--email/--password`. |
| Registration/verification emails | Never sent — the test server uses an in-process email sink. Verification is disabled; password-reset flows are not testable locally. |
| Google Sign-In fails | Expected against the local server (in the apps and in the `--web` browser client alike); use email/password. |
| `--web needs npm on PATH` | Install Node.js in the version range from `web/package.json` `engines.node`, or rerun without `--web`. |
| `port 5173 on 127.0.0.1 is busy` | A plain `npm run dev` (or another stack) already serves there. Stop it, or pass `--web-port`. |
| `the emulator exited during boot` | See `<state>/logs/emulator.log`. On Windows enable the **Windows Hypervisor Platform** feature (hardware acceleration); also check the AVD is not already open in Android Studio. |
| `AVD ... cannot be created` | Install the SDK *Command-line Tools* and a phone system image (`sdkmanager "system-images;android-36;google_apis;x86_64"`), or create the AVD in Android Studio's Device Manager. |
| Background `up` on Windows will not stop | Use `python tests/e2e/manual_stack.py stop`; `taskkill` without `/F` cannot end a console process. |
| State vanished after reboot (Linux/macOS) | `/tmp` may be cleared on reboot; use `--state-dir ~/mobaide-manual-e2e`. |

## Production safety

- Rollout writes **no tracked files**: the tracked `android/app/src/main/res/xml/network_security_config.xml` stays byte-for-byte intact (cleartext stays disabled for release), and both generated files are gitignored.
- Android release builds never include the `src/debug` source set, and release CI provides `CENTRAL_SERVER_URL` via environment variable, which outranks `local.properties`. `local.properties` points at the test stack only **while the stack is running**, but can linger with `--keep-android` or after a hard kill that skipped teardown: if you build a **local release APK** then, unset the line first / run `android --revert`.
- iOS has **no equivalent of the `src/debug` isolation**:
  - `Protocol.xcconfig` is the base configuration for *both* Debug and Release, so a lingering `Local.xcconfig` would repoint a local Release archive too.
  - Same mitigation — it is restored on stop; after `--keep-ios` or a hard kill, run `ios --revert` before archiving.
  - The script never writes `Protocol.xcconfig` itself (`protocol-integrity.yml` parses the tracked file for the protocol constants).
- The test server/agent state is fully isolated under the state dir; a co-installed production agent's `.env`, stores, and updater are never touched. Crash recovery kills only processes whose recorded pid **and** create time match a previous run of this script.
- The account password is a fixed, trivial dev-testing value (`Qwe>1234`), chosen for tester convenience over secrecy.
  - Because the default `up` exposes the server on the LAN and the owner account fronts a real shell on this machine, this well-known password lets anyone on the same Wi-Fi open a terminal here.
  - So keep the stack on a trusted network, bind loopback (`--host 127.0.0.1`, e.g. the emulator flow), or pin your own `--password` when that matters.
- If a shell still exports variables like `MOBAIDE_ENV`, `RELAY_*`, `JWT_SECRET_KEY`, the script sanitizes them out of the child processes.
- See [configuration-profiles.md](configuration-profiles.md): while the generated debug override exists, **debug builds ignore the profile-managed main network config**. Run `android --revert` before debug-testing a TLS profile.
