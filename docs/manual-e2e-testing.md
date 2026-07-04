# Manual E2E testing (real phone / emulator against a local stack)

`tests/e2e/manual_stack.py` boots the **real** central_server + **real** mobaide
agent on your PC and wires the **real Android app** to them, so a human tester
can exercise the full mobile ↔ server ↔ agent path hands-on: registration,
login, agent list, SHELL terminals and managed claude/codex/gemini sessions.
It is the manual-testing counterpart of the automated pytest harness documented
in [e2e-harness.md](e2e-harness.md) and reuses the same recipe
(`tests/e2e/conftest.py` + `tests/e2e/mock_mobile_client.py`) outside pytest.

One command, cross-platform (Windows / macOS / Linux), repeatable, and safe:
**no tracked file is ever modified and release builds are unaffected** (see
[Production safety](#production-safety)).

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
# 3. build + install the DEBUG app on a connected device
cd android
.\gradlew.bat :app:installDebug        # Windows
./gradlew :app:installDebug            # macOS / Linux
```

Then open the app on the phone, **register or log in** with the credentials
from the banner (default `dev@test.local` / `DevPass123!`), pick the
`manual-e2e-agent`, and start testing. `Ctrl+C` stops the stack; state
persists, so the next `up` reuses the same account, agent, and database — the
phone stays logged in.

## What the script does

1. Ensures both packages are installed editable (Python 3.12+).
2. Boots `python -m central_server` bound to `0.0.0.0:9000` with
   `MOBAIDE_ENV=test`: throwaway SQLite DB, in-process email sink (no SMTP),
   `EMAIL_VERIFICATION_REQUIRED=false`.
3. Registers (or logs back into) the tester account and creates (or reuses) the
   agent over the real REST flow, exactly like the Android app would.
4. Boots `python -m mobaide` in **production mode** — a **real PTY**, so SHELL
   sessions run a real shell and claude/codex/gemini sessions run the real
   CLIs — pointed at the local relay over `ws://127.0.0.1:9000`
   (`MOBAIDE_AGENT_ALLOW_INSECURE_WS=1`), with all agent state isolated under a
   throwaway `MOBAIDE_AGENT_PREFIX`.
5. Gates readiness on `/api/health` → `agents_online >= 1` (the same
   4012-safe gate the automated harness uses).
6. Syncs the two **gitignored** Android files (next section) and prints the
   READY banner, then streams `[server]` / `[agent]` logs until `Ctrl+C`
   (logs are also written under the state dir).

State lives in `<system-temp>/mobaide-manual-e2e` by default (`--state-dir` to
move it): `state.json` (JWT secret, account, agent id + token, last-used port),
`server/` (DB + manifest), `agent/` (isolated agent prefix), `logs/`. An
explicit `--port` is remembered; bare re-runs reuse it.

### Why the agent runs in production mode

`MOBAIDE_ENV=test` on the **agent** selects the fake echo PTY backend — no real
shells, no real CLIs — which is right for the automated harness but useless for
manual testing. The script therefore runs the agent in production mode by
default. The old copy-paste recipe that exported `MOBAIDE_ENV=test` for the
agent gave echo-only terminals. If you *want* that dependency-free mode (e.g.
to verify the infrastructure on a machine with no CLIs), use `--fake-pty`, and
`--fake-pty --smoke` to also drive one echo session end-to-end automatically.

This is safe on a machine with a production agent install: the auto-updater is
fail-closed (no trust key in the throwaway prefix), and the explicit child
environment + throwaway `MOBAIDE_AGENT_PREFIX` keep the test agent away from
any real install's `.env`, stores, and state.

## What the script writes into your checkout

Only two files, **both gitignored** — `git status` stays clean and there is
nothing to revert before committing:

| File | Content | Why it is safe |
|---|---|---|
| `android/local.properties` | `CENTRAL_SERVER_URL=http://<your-ip>:9000` merged in; every other line (e.g. `sdk.dir`) is preserved | gitignored; read only on developer machines (release CI passes the env var instead) |
| `android/app/src/debug/res/xml/network_security_config.xml` | generated debug-only override permitting cleartext HTTP | gitignored; the **debug source set is never merged into release builds**, and the tracked main config (cleartext disabled) is untouched |

The server URL is **baked into the APK at build time**
(`android/app/build.gradle.kts`), and Android blocks cleartext HTTP unless the
network security config allows it — these two files are exactly what makes a
DEBUG build reach `http://<your-PC>:9000`. After any URL change (new Wi-Fi
lease, different `--port`), **rebuild + reinstall the app**; the banner warns
when that is needed.

Undo both at any time:

```
python tests/e2e/manual_stack.py android --revert
```

## Connectivity modes

| Mode | Command | App URL | Notes |
|---|---|---|---|
| **LAN (default)** | `up` | `http://<PC-LAN-IP>:9000` | Phone and PC on the same Wi-Fi/subnet; allow the Windows firewall prompt (Private networks) |
| **Emulator** | `up --android-url emulator` | `http://10.0.2.2:9000` | `10.0.2.2` is the emulator's alias for the host; add `--host 127.0.0.1` for a loopback-only run with no firewall prompt |
| **USB / adb reverse** | `up --android-url reverse`, then `adb reverse tcp:9000 tcp:9000` | `http://127.0.0.1:9000` | No LAN or firewall involvement; re-run `adb reverse` after replugging |

`--advertise-ip <ip>` overrides LAN-IP auto-detection (useful with VPN/Hyper-V/
WSL adapters); the banner lists all candidate IPs to try.

## Day-2 commands

| Command | Effect |
|---|---|
| `up` | boot; reuses persisted account/agent/DB (phone stays logged in) |
| `up --fresh` | wipe state first: new secret, account, agent, empty DB |
| `up --fake-pty [--smoke]` | echo-backend infra check (no shells/CLIs needed) |
| `up --install-app` / `android --install-app` | also run `gradlew :app:installDebug` |
| `up --projects <paths>` | seed the agent's project list with real directories |
| `android` | (re)sync the two Android files without booting the stack |
| `android --revert` | remove the debug override, restore/remove `CENTRAL_SERVER_URL` |
| `status` | probe `/api/health`, show persisted identity |
| `clean` | kill stale processes, revert Android files, delete the state dir |

CLI sessions: SHELL sessions need nothing extra. claude / codex / gemini
sessions need that CLI installed on the PC (on `PATH`, or via
`MOBAIDE_CLAUDE_BIN` / `MOBAIDE_CODEX_BIN` / `MOBAIDE_GEMINI_BIN`, which the
script deliberately inherits from your shell). A missing CLI fails only at
session-create time with a `<cli>_cli_missing` marker.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `port 9000 is busy` | A previous crashed run (`manual_stack.py clean` reaps it), the root `docker-compose.yml` dev server, or another app. Or use `--port`. |
| Firewall prompt denied / never appeared | Windows Settings → Firewall → Allow an app → allow Python on **Private** networks; or avoid the firewall entirely with the emulator or adb-reverse mode. |
| Phone cannot connect | Open `http://<PC-IP>:9000/api/health` in the phone's browser. If it fails: same Wi-Fi? AP/client isolation on the router? VPN on either end? Windows network profile set to Public? Wrong NIC picked → `--advertise-ip`. |
| App shows network/cleartext errors | The installed build predates the file sync or is a RELEASE build. Re-run `python tests/e2e/manual_stack.py android`, then rebuild + reinstall the **debug** app. |
| IP changed (new DHCP lease) | Re-run `up` (it re-syncs and warns), rebuild + reinstall the app. |
| `could not log in ... state.json no longer match` | DB and state diverged (e.g. deleted one of them). `up --fresh`, or pass matching `--email/--password`. |
| Agent listed but offline in the app | Check `[agent]` log lines in the console or `<state>/logs/agent.log`. |
| Registration/verification emails | Never sent — the test server uses an in-process email sink. Verification is disabled; password-reset flows are not testable locally. |
| Google Sign-In fails | Expected against the local server; use email/password. |
| State vanished after reboot (Linux/macOS) | `/tmp` may be cleared on reboot; use `--state-dir ~/mobaide-manual-e2e`. |

## Production safety

- Rollout writes **no tracked files**: the tracked
  `android/app/src/main/res/xml/network_security_config.xml` stays byte-for-byte
  intact (cleartext stays disabled for release), and both generated files are
  gitignored.
- Release builds never include the `src/debug` source set, and release CI
  provides `CENTRAL_SERVER_URL` via environment variable, which outranks
  `local.properties`. (Corollary: don't build a **local release APK** for other
  purposes while `local.properties` points at your test stack, or unset the
  line first / run `android --revert`.)
- The test server/agent state is fully isolated under the state dir; a
  co-installed production agent's `.env`, stores, and updater are never touched.
- If a shell still exports variables from the old copy-paste recipe
  (`MOBAIDE_ENV`, `RELAY_*`, `JWT_SECRET_KEY`, ...), the script sanitizes them
  out of the child processes and applies its own explicit values.
- Interaction with [configuration-profiles.md](configuration-profiles.md):
  while the generated debug override exists, **debug builds ignore the
  profile-managed main network config** (Android source-set precedence). Run
  `android --revert` before debug-testing a TLS profile.
