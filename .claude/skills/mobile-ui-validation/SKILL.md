---
name: mobile-ui-validation
description: Use whenever a UI component or flow of the Android (`android/`) or iOS (`ios/`) app must be checked to really work, or when E2E tests are created or run. Requires deploying the infrastructure locally with `tests/e2e/manual_stack.py` and using the Android emulator / iOS simulator — never a production server.
---

# Mobile UI validation & E2E

To verify that a mobile UI component works, or to create E2E tests, deploy the infrastructure
(central_server + agent) **locally** with `tests/e2e/manual_stack.py` and run the real app on the
**Android emulator** (for `android/` changes) and the **iOS simulator** (for `ios/` changes).
Full reference: `docs/manual-e2e-testing.md`; the automated pytest harness (`tests/e2e/`,
`python -m pytest tests/e2e/ -v`) is described in `docs/e2e-harness.md`.

```bash
# 1. boot the devices (AVD `Mobaide-E2E`, simulator `iPhone 16`; adb/emulator are not on PATH here)
"$ANDROID_HOME/emulator/emulator" -avd Mobaide-E2E &      # skip if `adb devices` already lists one
xcrun simctl boot 'iPhone 16'; xcrun simctl bootstatus 'iPhone 16' -b

# 2. deploy the local stack (loopback; emulator reaches it via 10.0.2.2, simulator via 127.0.0.1)
~/venvs/main/bin/python tests/e2e/manual_stack.py up --host 127.0.0.1 --android-url emulator &
echo $! > stack.pid      # run detached; wait until `manual_stack.py status` exits 0 (READY)

# 3. build + install the debug apps against that stack
~/venvs/main/bin/python tests/e2e/manual_stack.py android --install-app
~/venvs/main/bin/python tests/e2e/manual_stack.py ios --install-app

# 4. exercise the UI on the emulator/simulator (adb / `xcrun simctl` / `axe`), then stop the stack —
#    this restores android/local.properties and ios/Config/Local.xcconfig
kill -TERM "$(cat stack.pid)"
```

Login shown in the READY banner: `dev@test.local` / `Qwe>123`.
