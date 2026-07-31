# iOS Client (Swift)

Native iOS client (SwiftUI, iOS 17+) with feature parity to the Android app as
the goal of the iOS series (CLA-97…CLA-106). The backend (`central_server/`
relay + `mobaide_agent/`) and the wire protocol (v6 during the series, v7
since CLA-155 — see `PROTOCOL.md`) are frozen — iOS conforms to them byte
for byte (with one additive exception:
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
CLA-103 replaced the Auto Scan placeholder with the live autoscan monitor —
an "Active Auto Scans" card section atop the sessions list and a per-project
detail screen (phase timeline, auto-following ANSI log, 1 Hz countdown) —
backed by an app-scoped `AutoScanRepository` (`ws/events`-bus list state plus
REST seed, optimistic start/stop/run-now with typed 409/429 errors), a
dedicated `AutoScanWsClient` whose live per-project resume cursor and
two-layer seq dedupe fix the Android duplicate-log replay bug, and a
256-color `AnsiParser` (a deliberate superset of Android's minimal SGR
parser).
CLA-104 replaced the Settings and Support placeholders with their real
bodies: a `Form`-based Settings screen (account header, My Agents /
Templates / Devices nav rows, Help & Support, Privacy policy, Logout, and a
Danger-zone delete-account entry); the multi-step delete-account flow
(`DeleteAccountModel` — Explain + scheduled/immediate mode → password /
Google / **Apple** re-auth → typed-email confirmation, clearing the local
session immediately without waiting for the WS-4010 close); the Support mail
fallback chain (`MFMailComposeViewController` → `mailto:` → copy-address, via
a `MailComposing` seam) plus an `SFSafariViewController` web wrapper; and the
launch-time version gate (`VersionGateRepository.probe()` on
`AppContainer.start()` and on scene-foreground, driving the enhanced
`IncompatibleServerScreen` takeover with App Store + "What changed" links).
The account-deletion grace banner mounts on the authenticated agents list.
Reversing the iOS-04/iOS-08 decision, the Android `terminal_display_mode` /
`terminal_static_columns` preferences are re-added, restoring cross-platform
parity: a global Settings → Terminal display toggle between **Full Screen**
(columns auto-fit the viewport and reflow) and **Static** (a fixed column
count — default 120, adjustable 60–240 in steps of 10 — rendered wider than
the viewport and panned horizontally), persisted locally in `UserDefaults`
(default Static/120) and applied live to the SwiftTerm surface; since
SwiftTerm has no forced-columns API, Static wraps the terminal in a
horizontal-pan `TerminalPanScrollView`. The remote-agent-outdated
banner and the optional iTunes-lookup app-update nudge are deferred (CLA-120).
CLA-105 adds local notifications for session attention (iOS-09): an
`AttentionCoordinator` subscribes to the `AgentEventBus` and posts a
`UNUserNotificationCenter` alert per `session.attention` (content mirrors
Android's `AttentionNotifier`, keyed by `session_id` so a re-post replaces in
place), clearing it when a `session.updated` drops the attention. Foreground
alerts for the on-screen session are suppressed via `ForegroundSessionTracker`,
and a tapped alert deep-links to the session's terminal once `.fullyConnected`
(`DeepLinkCoordinator` stashes the id on a cold start and replays it). Because
iOS has no foreground-service equivalent, delivery is honest to the platform:
the events socket lives while the app runs plus a ~30 s `beginBackgroundTask`
grace (`AppLifecycleController`), and events missed while suspended are
re-derived from a `GET /api/sessions` snapshot on foregrounding — the
`attention` field is stamped on every snapshot for exactly this recovery,
posting the set the client never showed and removing alerts whose attention is
gone. Authorization is requested silently as `.provisional`, so background
alerts arrive quietly (no heads-up banner/sound until the user promotes them) —
a deliberate divergence from Android's loud runtime prompt.
CLA-106 (iOS-10) closes the suspended-app gap with APNs alert pushes: the
agent mirrors each `session.attention` as a `user_event` control frame, and
the central server broadcasts it on `/relay/ws/user-events` — pushing via APNs
only when the user has **no** live user-events socket (so a foregrounded app
never double-alerts; see PROTOCOL.md's proto-v6 entry for the full contract).
On the client, `PushAppDelegate` (a `@UIApplicationDelegateAdaptor`) receives
the device token, `PushRegistrationCoordinator` registers for remote
notifications once signed in (`.userOnly`+) and POSTs the hex token to
`POST /api/devices/{device_id}/push-token` — re-POSTing per launch and on
rotation, best-effort DELETEing it on explicit sign-out — and
`PushEnvironment` picks `sandbox` vs `production` from the embedded
provisioning profile. A remote alert carries `session_id` in its custom data
and its `apns-collapse-id` equals the session id, so tap deep-links, foreground
suppression, replace-in-place, and the reconcile loop all reuse the iOS-09
pipeline unchanged. The entitlements file gains `aps-environment`
(`development`; signing rewrites it for distribution builds).
CLA-124 (revised scope) ports Android's explicit-only keyboard model to the
terminal: the soft keyboard raises solely from explicit user action — the
composer field, or a show-keyboard toggle in the `InputBar` that makes the
SwiftTerm view itself first responder so typed keys stream to the PTY as `i`
frames (gated on connection live + session running, keeping replay-era
auto-replies off the wire) — and never from tapping the terminal (a
`keyboardGate` on `canBecomeFirstResponder`, which also keeps SwiftTerm's
sticky-Ctrl accessory suppressed); the originally specced auto-activation was
reverted on Android as a UX regression and is deliberately not ported. The
composer collapses behind a "Command field" reveal chip, the control-key bar
gains a Backspace key (DEL `0x7f`), and leaving the session dismisses the
keyboard. Keyboard avoidance is pan-don't-resize (Android `ImeFloatingPan`
parity): the screen opts out of SwiftUI keyboard avoidance, lifts the UI
column by the keyboard overlap (`TerminalKeyboardObserver`), and
`TerminalPanScrollView` counter-pans the terminal so the cursor row plus two
slack rows stays visible (`TerminalKeyboardPan` math) — the grid never
resizes on keyboard show/hide, so keyboard transitions send no PTY resize
frames and no SIGWINCH.
A vertical-drag scroll ladder (`TerminalWheelScroll` plus a
`mobaide.verticalScroll` pan on `MobaideTerminalView`) ports Android
`TerminalView.doScroll`: full-screen TUIs (Claude Code, Codex, Gemini,
vim, less, htop) run on the alternate screen, which SwiftTerm creates
with no scrollback, so the terminal's own scroll view had nothing to
move and vertical drags did nothing in either display mode. A drag now
sends xterm wheel buttons 64/65 while the program has mouse reporting on
(coordinates pinned to the cell the drag started on), else arrow keys on
a bare alternate screen (`less`, `man`, `vim` — honoring
application-cursor mode), else scrolls SwiftTerm's own scrollback exactly
as before (plain shells are unchanged); a `gestureRecognizerShouldBegin`
override refuses SwiftTerm's scrollback pan whenever a program owns the
vertical axis. Two consequences match Android and are deliberate: local
scrollback is unreachable on the normal buffer while a program has mouse
reporting on, and a program that exits without restoring terminal modes
leaves the emulator in alt/mouse mode, so a scroll gesture writes arrow
keys or a mouse report to the shell prompt underneath.
Follow mode is driven from the terminal's own scroll view rather than from
SwiftTerm (`TerminalFollowScroll` plus a `UIScrollViewDelegate` on
`MobaideTerminalView`). SwiftTerm's iOS renderer draws from `contentOffset`
while its `scrollPosition` and `getLine(row:)` read `buffer.yDisp`, and nothing
in the package maps a drag onto `yDisp` — so `TerminalViewDelegate.scrolled`
never fired for a touch, the jump-to-bottom button was unreachable and
`jumpToBottom()` was a no-op. The app claims the scroll-view `delegate` slot
(SwiftTerm declares the conformance but implements no method of it and never
assigns it) and reports a position only while `isDragging`/`isDecelerating`,
which is the same feedback-loop guard as the Android fork's rule that
`setTopRow` must not report; the emulator-driven `scrolled` callback is now the
unread-row tally instead (Android `unreadRows`, badge capped at "999+"); like
Android's `scrollCounter` it counts every emulator scroll, so scrolling inside
a DECSTBM region counts rows the viewport never lost (CLA-183). While
follow mode is paused, an override of the `open var contentOffset` refuses
SwiftTerm's snap-to-tail writes (`updateScroller()` on every scrolled line,
`ensureCaretIsVisible()` on every byte sent — both `internal`, so unreachable
otherwise), matching Android's `TerminalEmulator.isAutoScrollDisabled`. Those
writes are told apart from a drag by a flag raised around the `feed` call
rather than by the gesture state alone: SwiftTerm snaps synchronously inside
`feed`, and UIKit reports the result as an ordinary scroll — so a line landing
mid-drag would otherwise read as the user returning to the tail. One
limitation is accepted: once the scrollback ring is full SwiftTerm trims from
the top without growing `contentSize`, so a held viewport drifts up a row per
trimmed line — Android compensates via `mTopRow`, which needs an index iOS
cannot read. Row hit-testing re-points `buffer.yDisp` at the rows actually on
screen for the duration of the synchronous `getLine` read, so tapping a prompt
still resolves the rendered line while scrolled into history; a tap below the
last line — the blank overscroll band — resolves nothing rather than folding
onto that line, since a tap is what executes it. That drawn-equals-resolved
guarantee holds against `terminal.buffer` and is suspended while a program
holds synchronized output (DEC mode 2026), where SwiftTerm draws a frozen deep
copy the app cannot reach (CLA-182).
CLA-136/CLA-138 port Android's terminal custom keyboard shortcuts: the
control-key row (now gated on `canSendTerminalBytes`, matching the wire)
gains a trailing "Custom shortcut" ellipsis trigger that opens a
chord-picker sheet — Ctrl/Alt/Shift modifiers (max two) over an
A–Z / 0–9 / F1–F12 / Tab / Enter / Space / Backspace key grid, with a live
byte preview that flags CSI-u encodings as "may not work in every CLI" and
discloses same-byte chords. Recents are device-local MRU per session kind
(cap 10); saved shortcuts render as buttons after the trigger (tap sends,
context menu Rename/Remove, Save/Saved toggle in the picker) and sync live
across devices through `/api/shortcuts` and the `shortcut.*` user events
(the CLA-137 server contract in PROTOCOL.md). Under the hood: a byte-exact
`KeyChordEncoder` port, a `ShortcutsRepository` cloned from the templates
stack and wired to the `UserEventBus` (refresh-on-connect,
clear-on-logout), `TerminalSessionModel`'s `resolvedKind` so shortcut
lists key off the session's true kind, and golden fixtures for the four
shortcut DTOs.
CLA-155 ports the custom AI agent types client half (proto-v7; server
contract CLA-153, Android reference CLA-154): `SessionKind` becomes an open
enum that preserves unknown wire values verbatim as `.custom(String)` (the
old coerce-to-`.claude` fallback is gone), `SessionResponse` gains the
nullable `kind_label` name snapshot, and the `CustomAgentTypeDto` family
plus `/api/custom-agent-types` CRUD in `CentralAPI` back an
account-generation-guarded `CustomAgentTypesRepository` wired to the
`UserEventBus` (`custom_type.created/updated/deleted` demux; a deletion
also purges that kind's saved shortcuts locally, mirroring the server-side
cascade). The create-session mode step renders one card per custom type
plus an "Add custom agent…" card opening `CustomAgentTypeEditorSheet`
(create/edit via long-press context menu, delete with a confirmation that
spells out the shortcut cascade); custom launches keep the worktree toggle
and run as plain terminal sessions; session cards label custom kinds via
`kind_label` → live registry name → `Custom`; the control-key bar falls
back to a kind-neutral 9-key set for custom (and not-yet-resolved) kinds;
and `APP_PROTOCOL_VERSION` bumps 6 → 7, with four new central DTO fixtures
registered in the golden-fixture registry.
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
│   ├── Assets.xcassets/          # AppIcon — single 1024×1024 opaque icon (iOS 17 needs no smaller
│   │                             # sizes); wired via ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon
│   ├── Core/Concurrency/         # Broadcaster (StateFlow/SharedFlow replay parity)
│   ├── Core/Protocol/            # TerminalFrame, JSONValue, WireJSON, ProtocolVersion
│   ├── Core/Networking/          # WebSocketCore, NWWebSocketTransport, RelayRouting,
│   │                             # AgentAPI, CentralAPI, HTTPClient, TokenProviding
│   ├── Core/Storage/             # KeychainStoring, SecItemKeychainStore
│   ├── Data/                     # Auth (SessionManager, token/identity stores, SignInService,
│   │                             # Google/Apple sign-in clients), Events (AgentEventBus,
│   │                             # UserEventBus), Local, Security, Repository
│   ├── Models/                   # one Swift file per Android dto/*.kt file (wire DTOs)
│   ├── Notifications/            # ForegroundSessionTracker, NotificationService,
│   │                             # AttentionCoordinator/Content/Suppression (iOS-09),
│   │                             # PushAppDelegate/PushRegistrationCoordinator/
│   │                             # PushEnvironment (iOS-10 APNs)
│   ├── UI/                       # Theme, Navigation (Router, RootView), Common, Onboarding,
│   │                             # Connections, Sessions (list + create wizard), Terminal
│   │                             # (SwiftTerm host, InputBar, keyboard pan, wheel scroll,
│   │                             # shortcut picker, session model/store), and the feature
│   │                             # screens (Agents, Projects, Devices, Templates, AutoScan,
│   │                             # Settings, Support, Update)
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

APNs sandbox testing (iOS-10): the simulator has no real APNs, but the
notification-handling path (banner, collapse-by-session, tap deep link) can be
exercised with a payload file — `xcrun simctl push <udid> com.mobaide.app
payload.apns` where `payload.apns` contains
`{"aps": {"alert": {"title": "Your move", "body": "proj finished its turn — tap
to continue"}, "sound": "default", "thread-id": "s1"}, "session_id": "s1",
"agent_id": "a1"}` (the `Simulator Target Bundle` key or the bundle-id argument
selects the app). End-to-end delivery needs a real device plus a server
configured with `APNS_KEY_PATH` / `APNS_KEY_ID` / `APNS_TEAM_ID` /
`APNS_TOPIC` and `APNS_USE_SANDBOX=true` for Xcode-installed builds
(`central_server/.env.example`): run the app once signed in (the token POST
logs `device.push_token_registered` server-side), suspend or force-quit it,
trigger attention on a session, and the alert should arrive within seconds;
tapping it must open the session's terminal.

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
  synthesis would throw); `SessionKind` preserves unknown raw values
  verbatim as `.custom` (proto-v7 `SessionKindSerializer` parity; an
  explicit-null `kind` still falls back to `.claude`); `ProjectUpdate` relies on
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
- `frames/autoscan_frames.json` — `ws/autoscan` channel JSON frames:
  `cases[]` of `{name, wire, expect}` covering `snapshot` / `caught_up` /
  `event` (all six `ScanProgressPayload` kinds) plus malformed-drop cases,
  asserted against the decoded `AutoScanFrame`.
- `ansi/ansi_sgr.json` — SGR + 256-color ANSI goldens: `cases[]` of
  `{name, input, runs}` mapping a raw log line (ESC as `\u001b`) to the
  expected `AnsiParser` runs, with fg/bg as integer `[r,g,b]` channels.
- `relay/relay_mapping.json` — relay path rewrites, WS URL construction, and
  REST base derivation tables.

Adding a DTO type: create its fixture JSON and register the type in
`ios/MobaideTests/Fixtures/FixtureRegistry.swift` — the DTO fixture test
fails if either side is missing (bidirectional coverage check).
