# Cross-platform E2E harness (`tests/e2e/`)

Boots the **real** central server and **real** mobaide agent as subprocesses on
ephemeral loopback ports and drives the full **mobile ↔ server ↔ agent** path
with a headless Python client speaking the actual REST + WebSocket protocol the
Android app uses. No Docker, no PowerShell, no real shells — and guaranteed
teardown with no orphan processes/files.

This catches integration regressions that the in-process ASGI tests
(`tests/test_e2e_autoupdate.py`) cannot: the real relay auth handshake, proxy
routing, binary envelope framing, and the Windows `WindowsSelectorEventLoop`
requirement (only the subprocess path exercises it).

For hands-on **manual** testing of the same stack from a real phone or emulator,
see [manual-e2e-testing.md](manual-e2e-testing.md) — `tests/e2e/manual_stack.py`
reuses this harness's recipe outside pytest.

## What it does

`tests/e2e/conftest.py`'s session-scoped `e2e_stack` fixture:

1. picks two free loopback ports and a per-run temp dir,
2. boots `python -m central_server` (test mode, tmp DB/manifest, email off),
3. provisions an owner user + agent over the **real REST flow**
   (`POST /api/auth/register` → `POST /api/agents`),
4. boots `python -m mobaide` pointed at the local `ws://` relay with the agent's
   credentials and `MOBAIDE_ENV=test` (fake PTY, insecure-ws opt-in, tmp state),
5. gates readiness on `GET /api/health` → `agents_online >= 1` (not bare HTTP 200,
   which races into WS close 4012), then yields a `MockMobileClient`.

`agents_online`/`agents_registered` are admin-gated (anonymous callers only get
`{"status": "ok"}`), so the server is booted with `ADMIN_SEED_EMAIL`/
`ADMIN_SEED_PASSWORD` set to `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD`, and step 5
polls with that admin's token (`client.login(...)`), not the mobile-owner's —
if this wiring is ever dropped, the readiness wait fails silently as a 45s
timeout rather than an auth error.

`tests/e2e/test_e2e_session_echo.py` then mints a mobile token, creates a **SHELL** session
via `POST /proxy/{agent_id}/api/sessions` (the agent's `FakePtyBackend` — no real
shell, no `claude`/`codex` CLI), attaches the relay session WebSocket, and asserts
a sent marker is echoed back through the binary `o` (output) frame.

## Prerequisites

- Python 3.12+, with **both** packages installed editable from the repo root:
  ```
  python -m pip install -e 'mobaide_agent[dev]'
  python -m pip install -e 'central_server[dev]'
  ```
  The `[dev]` extras add `pytest-xprocess` and `psutil` (the only deps unique to
  this harness; `websockets`, `httpx`, `pytest-asyncio`, `pytest-timeout` are
  already present).
- No `claude`/`codex` CLI, no SMTP server, no Docker, no Caddy/TLS required — the
  harness uses the agent's fake PTY (SHELL sessions) and the server's in-process
  email sink, all over plaintext loopback.

## Run

From the repo root:

```
python -m pytest tests/e2e/ -v
```

Do **not** pass `-p no:cacheprovider` — `pytest-xprocess` stores its per-process
logs via the cache plugin and needs it enabled.

## Cleanup

Teardown is automatic (process-tree kill + temp-dir removal) on pass, fail, or
Ctrl-C. If a run is hard-killed and leaves a straggler, clear it with:

```
python -m pytest --xshow    # list xprocess-managed processes
python -m pytest --xkill     # terminate them
```

xprocess logs live under `.pytest_cache/` (gitignored).

## CI

`.github/workflows/e2e-harness.yml` runs this suite on an
`ubuntu-latest` / `windows-latest` / `macos-latest` matrix (Python 3.12) and runs
`pytest --xkill` as an `always()` post-step to clear any stragglers.

## Notes / gotchas

- **Windows event loop:** the headless client uses the `websockets` asyncio client,
  which hangs under the Proactor loop, so `conftest.py` forces
  `WindowsSelectorEventLoopPolicy` at import. The agent subprocess forces the same
  policy internally.
- **Bind to `127.0.0.1` only** (the harness does) so the first loopback bind does
  not trigger the Windows Defender Firewall "allow public networks" prompt.
- **SHELL, not managed:** managed (claude/codex) sessions still resolve a real CLI
  binary even in test mode; the harness drives SHELL sessions to avoid that. See
  the OS-prep notes for details.
