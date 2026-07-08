# iOS Client (Swift)

Native iOS client (SwiftUI, iOS 17+) with feature parity to the Android app as
the goal of the iOS series (CLA-97…CLA-106). The backend (`central_server/`
relay + `mobaide_agent/`) and the wire protocol (v6, see `PROTOCOL.md`) are
frozen — iOS conforms to them byte for byte (with one additive exception:
CLA-99 added `POST /api/auth/apple` to the server, mirroring the Google
verification path). CLA-97 bootstrapped the project and ported the networking
spine; CLA-98 added the auth/session core (Keychain stores, SessionManager,
the two WebSocket event buses) and a navigable placeholder UI shell; CLA-99
replaced the six onboarding placeholders with the real screens (login,
register, verify-email, forgot/reset password, connection switcher) and the
shared `SignInService` (Google Sign-In ≥ 9.0 with a custom nonce + Sign in
with Apple); CLA-100 replaced the three main placeholders with their real
bodies — the SwiftTerm-backed terminal, the sessions list, and the
create-session wizard; CLA-101 replaced the Projects and Git placeholders
with the bus-driven Projects management screen (create/edit form sheet,
swipe-delete Undo, drag-reorder) and the Git branches/worktrees screen
(long-press multi-select, deferred batch delete with force/busy handling),
backed by an app-scoped `ProjectsRepository` and a reusable `UndoSnackbar`;
CLA-102 replaced the Agents, Devices, and Templates placeholders with the
QR-pairing Agents screen (in-app VisionKit/AVFoundation scanner gated by a
new `NSCameraUsageDescription`, Trust-On-First-Use trust-key confirmation,
and add/re-register/delete plus one-time-credentials and install-commands
sheets), the optimistic-revoke Devices list, and the favorites/all Templates
screen (create/edit/delete with live sync), backed by app-scoped
`AgentRepository`/`TemplatesRepository` stores wired to the `UserEventBus`.
Remaining feature screens land in later issues.

## Layout

```
ios/
├── Config/Protocol.xcconfig      # APP_PROTOCOL_VERSION / MIN_SERVER_PROTOCOL_VERSION
│                                 # + optional #include of Local.xcconfig (gitignored dev overrides;
│                                 #   see Config/Local.xcconfig.example)
├── Mobaide.xcodeproj             # hand-authored; objectVersion 70, Xcode 16 synchronized folder groups;
│                                 # SPM: GoogleSignIn-iOS ≥ 9.0, SwiftTerm 1.13.0
│                                 #   (+ swift-argument-parser transitively; Package.resolved committed)
├── Mobaide/
│   ├── App/                      # entry point, AppConfig, AppContainer (manual DI), AuthStateModel
│   ├── Core/Concurrency/         # Broadcaster (StateFlow/SharedFlow replay parity)
│   ├── Core/Protocol/            # TerminalFrame, JSONValue, WireJSON, ProtocolVersion
│   ├── Core/Networking/          # WebSocketCore, NWWebSocketTransport, RelayRouting,
│   │                             # AgentAPI, CentralAPI, HTTPClient, TokenProviding
│   ├── Core/Storage/             # KeychainStoring, SecItemKeychainStore
│   ├── Data/                     # Auth (SessionManager, token/identity stores, SignInService,
│   │                             # Google/Apple sign-in clients), Events (AgentEventBus,
│   │                             # UserEventBus), Local, Security, Repository
│   ├── Models/                   # one Swift file per Android dto/*.kt file (wire DTOs)
│   ├── Notifications/            # ForegroundSessionTracker
│   ├── UI/                       # Theme, Navigation (Router, RootView), Common, Onboarding,
│   │                             # Connections, Sessions (list + create wizard), Terminal
│   │                             # (SwiftTerm host, InputBar, session model/store), Placeholders
│   │                             # (remaining feature screens)
│   └── Support/                  # Info.plist (protocol keys, CentralServerURL, Google client ids,
│                                 # reversed-client-id URL scheme) + Mobaide.entitlements (SIWA)
└── MobaideTests/                 # unit tests + fixture round-trip suites
tests/protocol_fixtures/          # language-neutral golden fixtures (see below)
```

Both `Mobaide/` and `MobaideTests/` are Xcode 16 synchronized folder groups:
files added on disk join the right target automatically — no pbxproj edits.
The test bundle pulls `tests/protocol_fixtures/` in wholesale through a folder
reference, so new fixture files need no project changes either.

## Build / test

```sh
# unit tests (the CLA-97 acceptance command; any available simulator works)
xcodebuild -project ios/Mobaide.xcodeproj -scheme Mobaide \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  CODE_SIGNING_ALLOWED=NO test
```

First-time simulator setup: `xcodebuild -downloadPlatform iOS` (one-time,
~8 GB), then `xcrun simctl list devices available`; create the device if it is
missing: `xcrun simctl create 'iPhone 16'
com.apple.CoreSimulator.SimDeviceType.iPhone-16`. If the first run fails with
"Application failed preflight checks", pre-boot: `xcrun simctl boot <udid> &&
xcrun simctl bootstatus <udid> -b` and retry.

CI: `.github/workflows/ios-build.yml` runs the same command on `macos-latest`
with the newest installed Xcode 26.x selected at runtime (the runner pool
currently mixes macos-15 and macos-26 image snapshots whose 26.x minors
differ; the major is pinned and the job fails loudly if 26.x disappears),
creating the iPhone 16 simulator device when the image doesn't pre-create
it. SPM dependencies resolve over the network on the runner; the job passes
`-onlyUsePackageVersionsFromResolvedFile` so the committed `Package.resolved`
pins the versions and drift fails loudly. The pbxproj stays at
`objectVersion = 70` (the minimum for synchronized folder groups) so any
Xcode ≥ 16 can still open the project. `protocol-integrity.yml` additionally
asserts the xcconfig protocol constants match Android's `build.gradle.kts`
values.

## Sign-in configuration

The repo ships with no OAuth secrets; unconfigured builds show a "not
configured" message on the Google button (Android parity), while Sign in with
Apple needs no client-side config beyond the entitlement.

- **Local overrides** live in `ios/Config/Local.xcconfig` (gitignored,
  `#include?`-d by `Protocol.xcconfig`; template:
  `ios/Config/Local.xcconfig.example`): `CENTRAL_SERVER_URL`,
  `GOOGLE_IOS_CLIENT_ID`, `GOOGLE_SERVER_CLIENT_ID`,
  `GOOGLE_REVERSED_CLIENT_ID` (the URL scheme for the sign-in round trip).
  `GOOGLE_SERVER_CLIENT_ID` must equal the server's `GOOGLE_CLIENT_ID` — it
  becomes the ID token's `aud`.
- **Apple, server side**: set `APPLE_CLIENT_ID` (the iOS bundle id,
  `com.mobaide.app`) on `central_server` to enable `POST /api/auth/apple`.
  The endpoint verifies the identity token against Apple's JWKS (PyJWT,
  cached keys), checks the client nonce embedded verbatim in the token, and
  provisions users through the same find-or-create/link-by-verified-email
  path as Google (`users.apple_sub`, migration `0012` / user_version 14).
  Account-deletion re-auth accepts `method="apple"` (consumed by iOS-08).
- **Nonce scheme (both providers)**: `AuthNonce.generateNonce()` (SHA-256 hex
  of 32 CSPRNG bytes, Android parity) is embedded verbatim in the provider ID
  token; for Apple the client also POSTs it so the server can verify the
  match plus `iat` freshness.

## Protocol-parity approach

Android (`android/app/src/main/java/com/mobaide/app/data/network/`) is the
reference implementation; the Swift port mirrors it structurally so drift is
reviewable file by file:

- **Wire keys are Swift property names verbatim** (snake_case, no CodingKeys,
  no key strategy) — the same rule the Kotlin DTOs use.
- **kotlinx parity**: unknown JSON keys are ignored (`JSONDecoder` default);
  every Kotlin-defaulted field decodes via `decodeIfPresent ?? default` in a
  hand-written `init(from:)` (kotlinx fills defaults for missing keys — Swift
  synthesis would throw); `SessionKind` coerces unknown raw values to
  `.claude` (`coerceInputValues` parity); `ProjectUpdate` relies on
  synthesized `encodeIfPresent` so nil fields vanish (three-state PATCH),
  while `ProjectWrite.default_flags` is non-optional and always serialized.
- **Terminal frames** (`TerminalFrame.swift`): type byte + payload; encode
  `i`/`x`/`m`/`r`, decode `o`/`s`/`b`/`e`, nil for anything else; resize is
  `[0x72, rows>>8, rows&0xFF, cols>>8, cols&0xFF]`.
- **WebSocketCore** mirrors `WebSocketConnection`: 1 s→30 s doubling backoff
  (reset on open and forceReconnect), a generation counter that drops events
  from superseded sockets, terminal close codes 4001/4010/4002 (HTTP 401 at
  upgrade ≡ 4001), and 4011/4012 deliberately falling through to reconnect.
- **RelayRouting** ports `mapToRelayPath` and the URL assembly, including the
  raw (never percent-encoded) `?token=`/`&token=` append and the
  `{relay}/proxy/{agentId}` REST base.

Known deliberate deviations from Android's bytes (server-verified safe):

- Swift always encodes non-optional defaulted request fields (e.g.
  `rows: 24`) where kotlinx omits values equal to their defaults; the server
  defaults are identical, so semantics match.
- `JSONValue` canonicalizes integral floats: `1.0` on the wire re-encodes as
  `1`. Avoid `x.0` literals in fixtures; use fractional values to exercise
  doubles.
- Network.framework cannot surface the HTTP status of a rejected WS upgrade,
  so upgrade-time rejections surface as generic transport failures (reconnect
  path) rather than `tokenRejected`. This matches Android in practice: the
  backend closes unauthenticated handshakes before accepting, the ASGI server
  collapses that into a non-401 HTTP rejection, and OkHttp's 401-only check
  falls through to reconnect too — terminal auth handling lives in the
  token-refreshing URL provider on both platforms. The 401≡tokenRejected
  mapping stays in `WebSocketCore` (fake-tested) for transports and
  intermediaries that do surface a real 401.
- `AgentAPI`'s auth skip-list matches the percent-decoded URL path where
  Android matches the encoded path. Divergence is reachable only via a
  project name embedding an encoded auth suffix (e.g. `x%2Fapi%2Fauth%2Flogin`)
  and fails closed (Bearer withheld → 401). Known, deliberately left as-is;
  fix if it ever matters: `url.path(percentEncoded: true)`.
- `WebSocketCore.events` is an unbounded `AsyncStream` where Android bounds
  its flow at 101 events with drop-newest. Attach exactly one prompt consumer
  to `events` before `connect()`. CLA-98 resolved the backpressure decision:
  `events` stays unbounded — each event bus is the sole, promptly-draining
  consumer of a fresh core per connection cycle. CLA-100 extended the same
  rule to the terminal channel — `TerminalSessionModel` is its sole
  prompt-draining consumer, so `events` stays unbounded there too.

## Golden fixtures (`tests/protocol_fixtures/`)

Language-neutral goldens every later iOS issue (and potentially Android)
tests against:

- `dto/{agent,central}/<TypeName>.json` — one file per DTO type:
  `cases[]` of `{name, wire, decoded_canonical?}`. Tests decode `wire`,
  re-encode, and compare JSON trees (key-order-insensitive,
  null-vs-absent-sensitive) against `decoded_canonical ?? wire`. Use
  `decoded_canonical` when decoding materializes defaults or coerces enums.
- `frames/terminal_frames.json` — binary frame samples as hex strings
  (`encode` and `decode` case lists).
- `relay/relay_mapping.json` — relay path rewrites, WS URL construction, and
  REST base derivation tables.

Adding a DTO type: create its fixture JSON and register the type in
`ios/MobaideTests/Fixtures/FixtureRegistry.swift` — the DTO fixture test
fails if either side is missing (bidirectional coverage check).
