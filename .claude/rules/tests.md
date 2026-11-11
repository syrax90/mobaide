---
paths:
  - "mobaide_agent/tests/**"
  - "central_server/tests/**"
  - "tests/**"
  - "android/**/src/test/**"
  - "ios/MobaideTests/**"
  - "web/src/**/*.test.ts"
  - "web/src/**/*.test.tsx"
  - "web/e2e/**"
---

# Test rules

Write a test only when a break would hurt users or the wire contract and could slip past review:

- Security: auth, tokens, pairing, trust, throttles, path and input validation.
- Wire protocol, API contracts and golden fixtures.
- Data integrity: persistence, migrations, install / update / uninstall safety.
- State machines and concurrency invariants (sessions, PTY sizing, reconnect, sync).
- A real bug that was fixed and could come back.

Do not write tests for:

- Getters, defaults, constants, enum mappings, simple wrappers.
- Library, framework or OS behaviour, or assertions on your own mocks.
- UI copy, styles, colours, sizes, log wording.
- Inputs that cannot happen, or one more variant of a case already covered.

Prefer one strong test (or a small parametrize) over many near-copies. Tests must be deterministic and run on CI (`.github/workflows/unit-tests.yml`, `ios-build.yml`, `web-ci.yml`): no real network, no real sleeps, no host-specific paths. Fix or delete a failing test; never leave it red.
