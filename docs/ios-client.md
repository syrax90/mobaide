# iOS Client (Swift)

Native iOS client (SwiftUI, iOS 17+) with feature parity to the Android app as
the goal of the iOS series (CLA-97…CLA-106). The backend (`central_server/`
relay + `mobaide_agent/`) and the wire protocol (v6, see `PROTOCOL.md`) are
frozen — iOS conforms to them byte for byte. CLA-97 bootstrapped the project
and ported the networking spine; UI, auth/session logic, and event buses land
in later issues.

## Layout

```
ios/
├── Config/Protocol.xcconfig      # APP_PROTOCOL_VERSION / MIN_SERVER_PROTOCOL_VERSION
├── Mobaide.xcodeproj             # hand-authored; objectVersion 70, Xcode 16 synchronized folder groups
├── Mobaide/
│   ├── App/                      # SwiftUI entry point (placeholder until iOS-02+)
│   ├── Core/Protocol/            # TerminalFrame, JSONValue, WireJSON, ProtocolVersion
│   ├── Core/Networking/          # WebSocketCore, NWWebSocketTransport, RelayRouting,
│   │                             # AgentAPI, CentralAPI, HTTPClient, TokenProviding
│   ├── Models/                   # one Swift file per Android dto/*.kt file (wire DTOs)
│   └── Support/Info.plist        # only the two protocol keys (merged with the generated plist)
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
pinned to Xcode 16.4, so the project format and the Swift 6 sources must stay
compatible with both Xcode 16.x and current Xcode. `protocol-integrity.yml`
additionally asserts the xcconfig protocol constants match Android's
`build.gradle.kts` values.

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
  so a relay-edge 401 at upgrade time surfaces as a generic transport failure
  (reconnect path) rather than `tokenRejected`; the server's primary
  auth-rejection path — accept then close 4001 — keeps its exact semantics.
- `AgentAPI`'s auth skip-list matches the percent-decoded URL path where
  Android matches the encoded path. Divergence is reachable only via a
  project name embedding an encoded auth suffix (e.g. `x%2Fapi%2Fauth%2Flogin`)
  and fails closed (Bearer withheld → 401). Known, deliberately left as-is;
  fix if it ever matters: `url.path(percentEncoded: true)`.
- `WebSocketCore.events` is an unbounded `AsyncStream` where Android bounds
  its flow at 101 events with drop-newest. Attach exactly one prompt consumer
  to `events` before `connect()`. The backpressure decision (bounded parity
  vs documented unbounded contract) is deferred to CLA-98, where the real
  terminal consumer lands.

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
