# iOS Client (Swift)

Native iOS client (SwiftUI, iOS 17+) with feature parity to the Android app as
the goal of the iOS series (CLA-97…CLA-106). The backend (`central_server/`
relay + `mobaide_agent/`) and the wire protocol (v7 since CLA-155 — see
`PROTOCOL.md`) are frozen. iOS conforms to them byte for byte, with one additive
exception: CLA-99 added `POST /api/auth/apple` to the server, mirroring the
Google verification path.

On this page:

- [Foundation and screens from CLA-97 to CLA-104](#foundation-and-screens-from-cla-97-to-cla-104)
- [Notifications and push for CLA-105 and CLA-106](#notifications-and-push-for-cla-105-and-cla-106)
- [Terminal interaction](#terminal-interaction)
- [Shortcuts, custom agents, selection and paste](#shortcuts-custom-agents-selection-and-paste)
- [Later wizard, pairing and composer features](#later-wizard-pairing-and-composer-features)
- [Layout](#layout)
- [Theme / design system](#theme--design-system)
- [Device support](#device-support)
- [Privacy manifest](#privacy-manifest)
- [Export compliance](#export-compliance)
- [Build / test](#build--test)
- [Sign-in configuration](#sign-in-configuration)
- [Protocol-parity approach](#protocol-parity-approach)
- [Golden fixtures (`tests/protocol_fixtures/`)](#golden-fixtures-testsprotocol_fixtures)

## Foundation and screens from CLA-97 to CLA-104

- **CLA-97 / CLA-98:** project bootstrap, networking spine, and the auth/session
  core (Keychain stores, SessionManager, the two WebSocket event buses).
- **CLA-99:** onboarding screens and the shared `SignInService` (Google Sign-In
  ≥ 9.0 with a custom nonce + Sign in with Apple).
- **CLA-100:** the SwiftTerm-backed terminal, the sessions list, and the
  create-session wizard.
- **CLA-101:** Projects management and Git branches/worktrees screens (deferred
  batch delete with force/busy handling), on an app-scoped `ProjectsRepository`
  and a reusable `UndoSnackbar`.
- **CLA-102:** Agents (QR pairing with an in-app scanner gated by
  `NSCameraUsageDescription` and Trust-On-First-Use trust-key confirmation),
  Devices (optimistic revoke) and Templates screens, on app-scoped
  `AgentRepository`/`TemplatesRepository` stores wired to the `UserEventBus`.
- **CLA-103:** live autoscan monitor (an "Active Auto Scans" section atop the
  sessions list, plus a per-project detail screen). `AutoScanRepository` takes
  list state from the `ws/events` bus plus a REST seed, with optimistic
  start/stop/run-now and typed 409/429 errors. `AutoScanWsClient` keeps a live
  per-project resume cursor with two-layer seq dedupe. `AnsiParser` is 256-color.
- **CLA-104:** Settings and Support.
  - Delete-account flow (`DeleteAccountModel`): Explain + scheduled/immediate
    mode, then password / Google / **Apple** re-auth, then typed-email
    confirmation. It clears the local session immediately, without waiting for
    the WS-4010 close. The grace banner mounts on the authenticated agents list.
  - Support mail fallback chain: `MFMailComposeViewController` → `mailto:` →
    copy-address (`MailComposing` seam), plus an `SFSafariViewController` wrapper.
  - Launch-time version gate: `VersionGateRepository.probe()` runs on
    `AppContainer.start()` and on scene-foreground, driving the
    `IncompatibleServerScreen` takeover with App Store + "What changed" links.

### Terminal display mode

Settings → Terminal display chooses between **Full Screen** (columns auto-fit
the viewport and reflow) and **Static** (a fixed column count, default 120,
adjustable 60–240 in steps of 10, panned horizontally via
`TerminalPanScrollView`, since SwiftTerm has no forced-columns API). The choice
persists in `UserDefaults` (default Static/120) and applies live. It mirrors
Android's `terminal_display_mode` / `terminal_static_columns`.

The remote-agent-outdated banner and the optional iTunes-lookup app-update nudge
are deferred (CLA-120).

## Notifications and push for CLA-105 and CLA-106

### Local attention alerts (CLA-105, iOS-09)

- `AttentionCoordinator` subscribes to the `AgentEventBus` and posts a
  `UNUserNotificationCenter` alert per `session.attention`, keyed by
  `session_id` so a re-post replaces in place. A `session.updated` that drops the
  attention clears it.
- `ForegroundSessionTracker` suppresses foreground alerts for the on-screen
  session.
- A tapped alert deep-links to the session's terminal once `.fullyConnected`
  (`DeepLinkCoordinator` stashes a `pendingRoute` on a cold start).

iOS has no foreground-service equivalent. The events socket lives while the app
runs, plus a ~30 s `beginBackgroundTask` grace (`AppLifecycleController`). Events
missed while suspended are re-derived on foregrounding from a
`GET /api/sessions` snapshot, whose `attention` field is stamped for this
recovery. The reconcile posts instances this device has not shown and removes
alerts whose attention is gone.

### Which attention instance was already shown

The snapshot's `attention` is *latched*: the agent clears it only on a client PTY
write or a `UserPromptSubmit`. Notification Center cannot say what was already
shown, because tapping or swiping an alert empties it. `AttentionSeenStore`
answers it:

- A per-agent `UserDefaults` record of the last attention instance surfaced per
  session, keyed by reason plus `exit_reason`, written by the live event path and
  the reconcile.
- Dropped for a session whose attention is observed clear (re-arming it), and
  cleared wholesale at sign-out.
- A first reconcile for an agent adopts the current state as a baseline without
  posting.

Deliberate gap: no attention identity rides the wire. A clear plus a re-latch of
the *same* reason between two of this device's observations reads as already
surfaced.

### Notification authorization

Authorization is requested as the full `[.alert, .sound, .badge]` on first
`.fullyConnected`, matching Android's loud runtime prompt. The request is
fire-and-forget and is never awaited on either serial loop. The system prompt
blocks on the user and cannot display while backgrounded. Awaiting it in
`handleAuth` would hold a logout or agent-switch teardown behind the dialog, and
awaiting it in `post` would hold the whole event loop.

- `post` drops alerts while authorization is undecided, starting its own request
  when it finds `.notDetermined`. A grant reconciles from a fresh snapshot to
  recover them.
- `AppContainer` late-binds `NotificationService`'s grant hook to
  `AttentionCoordinator.authorizationGranted()`, which ignores a grant once the
  agent connection is gone.

> [!WARNING]
> Do not request `.provisional`. It grants quiet-only delivery and also caps
> whatever `willPresent` returns down to `.list`, so foreground alerts are
> silent. An install still in that state is promoted by re-requesting without
> `.provisional` the first time an alert would post.

### APNs alert pushes (CLA-106, iOS-10)

The agent mirrors each `session.attention` as a `user_event` control frame. The
central server broadcasts it on `/relay/ws/user-events` and pushes via APNs to
every registered device **except** those holding a live user-events socket. So a
foregrounded app never double-alerts, and a sleeping second device is still
reached. See PROTOCOL.md's proto-v6 entry for the full contract.

The socket identifies itself by appending its registered `device_id` to the
`ws/user-events` URL (`UserEventBus.urlProvider`). The relay verifies it against
the caller's own device rows. Android sends no hint and needs none: it registers
no push token, so it is never a push target. Gating on the *account* instead
would let Android's always-on foreground service silence every push to the
iPhone.

### Push registration and routing on the client

- `PushAppDelegate` (a `@UIApplicationDelegateAdaptor`) receives the device
  token.
- `PushRegistrationCoordinator` registers once signed in (`.userOnly`+) and POSTs
  the hex token to `POST /api/devices/{device_id}/push-token`. It re-POSTs per
  launch and on rotation, and best-effort DELETEs it on explicit sign-out.
- `PushEnvironment` picks `sandbox` vs `production` from the embedded
  provisioning profile. The entitlements file has `aps-environment`
  (`development`; signing rewrites it for distribution builds).
- A remote alert carries `session_id` (its `apns-collapse-id` equals it), so
  deep-links, foreground suppression, replace-in-place and the reconcile loop
  reuse the iOS-09 pipeline.
- It also carries the `agent_id` the relay stamped. Tapping an alert from a
  different agent first connects to that agent (`connectAgentViaRelay`), then
  navigates on the `fullyConnected` transition. One attempt per tap; a
  root-level toast on failure. A locally-posted alert names no agent and is the
  connected one's.

### Delegate actor isolation

Both `UNUserNotificationCenterDelegate` witnesses must stay `@MainActor`, via an
isolated conformance (`NSObject, @MainActor UNUserNotificationCenterDelegate`).
`QrScannerView` and `TerminalHostView` use the same shape.

> [!WARNING]
> A nonisolated witness calls UIKit's completion handler from the cooperative
> pool. For a response that launched or resumed the app, UIKit then runs
> `_updateSnapshotAndStateRestorationWithAction:`, which asserts main-thread
> affinity and aborts the process. That path is skipped while the app is active.
> So an off-main handler looks harmless in the foreground and crashes on every
> tap from a closed app.

## Terminal interaction

### Explicit-only keyboard (CLA-124)

The soft keyboard raises only from explicit user action: the composer field, or a
show-keyboard toggle in the `InputBar`. The toggle makes the SwiftTerm view first
responder, so typed keys stream to the PTY as `i` frames (gated on connection
live + session running, which keeps replay-era auto-replies off the wire).

Tapping the terminal never raises it. A `keyboardGate` on
`canBecomeFirstResponder` enforces this and keeps SwiftTerm's sticky-Ctrl
accessory suppressed. Auto-activation was reverted on Android as a UX regression
and is deliberately not ported.

- The composer collapses behind a "Command field" reveal chip. The control-key
  bar has a Backspace key (DEL `0x7f`). Leaving the session dismisses the
  keyboard.
- **Dismiss on touch.** Touching the terminal (or a scroll drag) while a keyboard
  covers it dismisses the keyboard. The touch is spent, so it cannot also resend
  a prompt row, send a mouse click, or copy to the composer. A live selection
  keeps the touch. The InputBar's own controls, a hardware keyboard's assistant
  bar, and iPad's undocked/floating/split keyboards are excluded, since none of
  them cover the terminal. Android and web mirrors are not yet implemented
  (CLA-253 / CLA-254).
- **Keyboard avoidance** is pan-don't-resize (Android `ImeFloatingPan` parity).
  The screen lifts the UI column by the keyboard overlap
  (`TerminalKeyboardObserver`), and `TerminalPanScrollView` counter-pans the
  terminal so the cursor row plus two slack rows stays visible. The grid never
  resizes, so keyboard transitions send no PTY resize frames and no SIGWINCH.

### Sessions panel drawer

A left-edge swipe on the terminal (or the top-bar `terminal-sessions-button`)
opens a `SideDrawer` listing the connected agent's sessions, a New session
shortcut and the account's other agents.

- Running/starting rows have a Stop button (Stop / Force Stop / Cancel).
  Stopping the on-screen session moves to the nearest open session by panel
  position (below first, then above), and returns to the sessions list only when
  none remains.
- Picking a session swaps the route in place (`Router.replaceLast(with:)`).
  Picking an online agent connects to it and lands on its sessions list via
  `DeepLinkCoordinator.openSessions(ofAgent:)`.
- The swipe is a `UIScreenEdgePanGestureRecognizer` (`mobaide.sessionsDrawer`)
  that the terminal's other pans `require(toFail:)`. The interactive-pop swipe is
  disabled while the terminal is on top (`InteractivePopGestureDisabler`; the
  Back button stays).

### Vertical-drag scroll ladder

`TerminalWheelScroll` plus a `mobaide.verticalScroll` pan on
`MobaideTerminalView` port Android `TerminalView.doScroll`. Full-screen TUIs
(Claude Code, Codex, Gemini, vim, less, htop) run on the alternate screen, which
SwiftTerm creates with no scrollback, so the terminal's own scroll view has
nothing to move.

A drag takes the first matching arm:

1. While the program has mouse reporting on: xterm wheel buttons 64/65, with
   coordinates pinned to the cell the drag started on.
2. On a bare alternate screen (`less`, `man`, `vim`): arrow keys, honoring
   application-cursor mode.
3. Otherwise: SwiftTerm's own scrollback (plain shells).

A `gestureRecognizerShouldBegin` override refuses SwiftTerm's scrollback pan
whenever a program owns the vertical axis, and in Static mode on the horizontal
drags the outer `TerminalPanScrollView` claims, so exactly one of the two nested
pans owns any touch.

Consequences, deliberate and matching Android:

- Local scrollback is unreachable on the normal buffer while a program has mouse
  reporting on.
- A program that exits without restoring terminal modes leaves the emulator in
  alt/mouse mode, so a scroll gesture writes arrow keys or a mouse report to the
  shell prompt underneath.

### Fling momentum

Lifting the finger starts a momentum tail (Android `TerminalView.onFling`):
`TerminalWheelScroll.flingRows` turns the lift-off velocity into rows and
`flingSteps` spreads them over at most twelve 40ms frames. Nothing coasts below
250pt/s.

- A cancellable `Task` walks the schedule. It aborts if the ladder arm changes,
  if the view leaves its window, or on the next touch (tap-to-stop, a deliberate
  divergence from Android).
- `MobaideTerminalView` overrides SwiftTerm's `send(source:data:)` to coalesce
  the mouse reports of one burst into one WebSocket frame. It must flush through
  that override rather than `send(data:)`, whose `ensureCaretIsVisible()` would
  yank a paused viewport back to the tail.

### Tap ladder

A single tap ladders on the same three states.

- **Mouse-tracking arm.** `MobaideTerminalView.sendMouseClick(at:)` reports a
  left-button press and release on the tapped cell, in one write. This is how
  Claude Code, vim and htop move their own cursor. SwiftTerm's equivalent cannot
  serve: `neutralizeSwiftTermGestures()` disables its taps, and it reports only
  while first responder, which the explicit-only keyboard forbids.
- The `mobaide.mouseTap` recognizer does not wait on the double-tap timeout, and
  grants simultaneous recognition so it cannot block the row tap or double tap.
- **Row-tap submit** is confined to the plain-shell arm. On the alternate screen
  and under mouse reporting the rows are the program's own chrome, and
  submitting one would Ctrl+C it and retype the text.
- The row tap arrives only after the double-tap timeout, by which time the
  program may have changed the state the ladder reads. So the arm is latched at
  lift, and both evaluations must agree before anything is submitted.
- Double-tap copy-to-composer works in every mode. An `.x10`-only program gets
  neither a click (SwiftTerm suppresses presses in that mode) nor a submit.
- A touch that put a covering keyboard away is refused by every single-tap
  recognizer; a second touch ladders normally.

### Follow mode and the unread badge

Follow mode is driven from the terminal's own scroll view
(`TerminalFollowScroll` plus a `UIScrollViewDelegate` on `MobaideTerminalView`),
not from SwiftTerm. SwiftTerm's iOS renderer draws from `contentOffset`, while
its `scrollPosition` and `getLine(row:)` read `buffer.yDisp`, which as of 1.15
parks at the row the finger lifted on. So the scroll view is the only honest
source for how far up the reader is.

- The app claims the scroll-view `delegate` slot and reports a position only
  while `isDragging`/`isDecelerating` (a feedback-loop guard).
- The emulator-driven `scrolled` callback feeds the unread-row tally (badge
  capped at "999+"). It counts only scrolls that pushed a row into the
  scrollback (`TerminalFollowScroll.pushesRowIntoScrollback`, entry condition
  `scrollTop == 0`), so a DECSTBM region shift does not inflate it (CLA-183).
  Android also catches DECLRMM and still counts DECSTBM: the rule is in parity;
  the count is not.
- While follow mode is paused, an override of `open var contentOffset` refuses
  SwiftTerm's snap-to-tail writes (`updateScroller()`, `ensureCaretIsVisible()`).
  A flag raised around the `feed` call tells them apart from a drag, because
  SwiftTerm snaps synchronously inside `feed`. The same flag keeps SwiftTerm's
  automatic replies (DA, DECRQM, the `ESC[6n` cursor-position report) from
  counting as the user typing; they still reach the PTY (Android: CLA-184).

Accepted limitation: once the scrollback ring is full, SwiftTerm trims from the
top without growing `contentSize`, so a held viewport drifts up a row per
trimmed line. `Buffer.totalLinesTrimmed` (readable since SwiftTerm 1.15) is not
yet adopted for this.

### Row hit-testing and synchronized output

Hit-testing re-points `buffer.yDisp` at the rows actually on screen for the
synchronous `getLine` read, so tapping a prompt resolves the rendered line while
scrolled into history. A tap below the last line (the blank overscroll band)
resolves nothing, since on the plain-shell arm a tap executes the line.

While a program holds synchronized output (DEC mode 2026), SwiftTerm suppresses
paints while still parsing, so the row `getLine` returns is not the row on the
glass. `resolveRow` then resolves nothing, refusing both the single-tap execute
and the double-tap copy-to-composer.

- `TerminalSynchronizedOutput` recognizes the mode from the output bytes, as a
  deliberate superset of SwiftTerm's parser: a false positive costs one ignored
  tap, a false negative reopens the hole.
- The window closes at once on `ESC[?2026l` (Ink brackets every frame), and
  otherwise on SwiftTerm's own one-second snapshot timeout plus slack.
- The frozen state is latched at lift (`lastTapDuringFrozenDisplay`) and both
  evaluations must agree, as for the pointer-ownership arm.
- Android refuses the same way while `TerminalSession.isScreenUpdateDeferred()`
  holds (CLA-182).

## Shortcuts, custom agents, selection and paste

### Custom keyboard shortcuts (CLA-136/CLA-138)

- The control-key row (gated on `canSendTerminalBytes`) ends with a "Custom
  shortcut" ellipsis trigger opening a chord-picker sheet: Ctrl/Alt/Shift
  modifiers (max two) over an A–Z / 0–9 / F1–F12 / Tab / Enter / Space /
  Backspace key grid. A live byte preview flags CSI-u encodings as "may not work
  in every CLI" and discloses same-byte chords.
- Recents are device-local MRU per session kind (cap 10).
- Saved shortcuts render as buttons after the trigger (tap sends; context menu
  Rename/Remove). They sync live across devices through `/api/shortcuts` and the
  `shortcut.*` user events (the CLA-137 server contract in PROTOCOL.md), via a
  `ShortcutsRepository` on the `UserEventBus` (refresh-on-connect,
  clear-on-logout).
- `KeyChordEncoder` is a byte-exact port. `TerminalSessionModel.resolvedKind`
  keys shortcut lists off the session's true kind.

### Custom agent types (CLA-155)

Client half of custom AI agent types (proto-v7; server contract CLA-153, Android
reference CLA-154).

- `SessionKind` is an open enum: unknown wire values are preserved verbatim as
  `.custom(String)`. `SessionResponse` has the nullable `kind_label` name
  snapshot.
- `/api/custom-agent-types` CRUD in `CentralAPI` backs an
  account-generation-guarded `CustomAgentTypesRepository`, wired to the
  `UserEventBus` (`custom_type.created/updated/deleted`). A deletion also purges
  that kind's saved shortcuts locally, mirroring the server-side cascade.
- The same repository holds the per-account mode order
  (`GET/PUT /api/session-mode-order`). It keeps the higher `version` and
  reconciles on user-events connect and on `session_mode_order.updated`.
- `APP_PROTOCOL_VERSION` is 7, with four central DTO fixtures in the
  golden-fixture registry.
- The create-session mode step has one card per custom type plus an "Add custom
  agent…" card opening `CustomAgentTypeEditorSheet` (edit via long-press;
  delete confirmation spells out the shortcut cascade).
- The project and mode steps have a **Reorder** / **Done** toolbar toggle
  (`EditMode` + `.onMove`). One save per drop. The project step also mirrors
  `projects.changed` live. See "Reorderable New-Session Lists" in
  [features.md](features.md).
- Custom launches keep the worktree toggle and run as plain terminal sessions.
  Session cards label custom kinds via `kind_label` → live registry name →
  `Custom`. The control-key bar falls back to a kind-neutral 9-key set.

### Text selection (CLA-133 and CLA-63)

**Sticky selection mode.** Long-pressing selects the word under the finger and
latches a sticky selection mode. While it holds, drags move the two handles, and
the `gestureRecognizerShouldBegin` gate refuses the scrollback pan, the
wheel/fling ladder, both row taps, the mouse-click tap and the outer Static-mode
horizontal pan. The latch is a deliberate deviation from Android: on iOS the drag
is owned by `TerminalWheelScroll` whenever a TUI enables mouse reporting, so
without it selection would be dead in Claude Code and Codex sessions.

**Toolbar.** A floating SwiftUI toolbar offers **Send to Input · Copy · Paste**
(Android's items and order) plus a Cancel the latch needs. It is app-drawn
because system edit menus source items from the responder chain, which
`keyboardGate` closes: they would come up empty or raise the keyboard.

**Selection is app-owned**, because SwiftTerm's `SelectionService` is an
`internal` class.

- `TerminalSelection` holds the pure geometry over **content-absolute** rows (the
  space `resolveRow` and `Terminal.getText` share). `TerminalSelectionController`
  owns the gestures plus a non-interactive overlay that draws the highlight and
  handles (`TerminalView.draw` is `public`, not `open`, and early-returns under
  the Metal renderer).
- A `UITextLoupeSession` magnifier (iOS 17+) follows the snapped endpoint while a
  handle is dragged.
- Text comes from `Terminal.getText`, which joins soft-wrapped rows without a
  newline and adds one on hard breaks (Android's `joinBackLines = true`).

### Selection tracking while output streams

A live selection pins the viewport through `holdsAutoScroll` and follows its text
as the emulator scrolls. Rows renumber only once a line is recycled, counted from
`Buffer.totalLinesTrimmed` (public since SwiftTerm 1.15), not from `yDisp`, which
1.15 freezes for a whole tracked drag.

The alternate buffer has no scrollback, so SwiftTerm never counts a trim there,
yet its ring is full from the first line. It is its own arm: a Claude Code or
Codex selection shifts up one row per streamed line instead of being cancelled.

A selection is dropped whenever its content-absolute rows stop naming the text
they were taken from:

- a region (DECSTBM) scroll that overlaps it;
- scrolling out of the transcript;
- the onset of synchronized output, whose frozen snapshot `getText` cannot read;
- a screen-buffer swap (`bufferActivated`, the only hook), which would otherwise
  let Copy lift unrelated text after a TUI exits;
- a grid resize, a replay wipe on reconnect, or resuming follow mode.

### Paste

Paste routes through one canonical encoder, `TerminalPaste`, byte-exact with
Android's `TerminalEmulator.paste()`:

1. Strip ESC and C1 **first**. A clipboard carrying a live `ESC[201~` then
   degrades to literal text. It cannot close the bracket early and execute its
   tail.
2. Then `\r?\n` → `\r`.
3. Then wrap in `ESC[200~`/`ESC[201~`, only when the program enabled DECSET 2004.
4. Send the result as exactly **one** `i` frame.

Steps 1 and 2 are two ordered passes, as on Android: a fused walk would turn
`CR ESC LF` into two carriage returns.

**Single frame.** This is a deliberate divergence and a disconnect-safety
invariant. Android's writes reach a local PTY fd and are effectively atomic.
Here they cross a WebSocket and a relay whose send result the drain loop
discards. Losing only the closing `ESC[201~` would strand the program's line
editor in paste-collect mode and swallow every later keystroke as paste content.
`TerminalPaste.writes(for:bracketed:)` still returns three parts as the byte
oracle for the ported Android cases; the model joins them. Paste never routes
through `sendInput`, which would double-wrap the markers into literal
`200~`/`201~`.

**Size cap.** Pastes above 1 MiB are refused with a toast (Android has no cap).
The agent opens its relay tunnel with `max_size=4 MB`, and one oversized frame
trips `PayloadTooBig`. That closes the tunnel with 1009 and disrupts **every**
session on the agent.

**Entry points.** The selection toolbar and a Paste key in the control-key row
that needs no selection (iOS-first; the Android mirror is tracked separately).
`Clipboard.readText()` is called only from an explicit tap.

### OSC 52 clipboard access

OSC 52 is write-direction and live-only, matching Android.

- A program may put text on the pasteboard (marked sensitive: device-local, with
  an expiry), but only from live output. `clipboardCopy` refuses bytes fed inside
  `applyingEmulatorOutput(replayed:)`, because the agent replays its ring on every
  attach and one yank in that history would re-copy on every session open. Known
  gap (GitHub issue #634): a sequence straddling the replay→live boundary is
  attributed to the span its terminator lands in, and so is honoured.
- The read/query form `ESC]52;c;?ST` (implemented by SwiftTerm 1.15) is refused:
  `TerminalHostView.Coordinator.clipboardRead` returns `nil`. So nothing on the
  agent host can lift the pasteboard across the relay.

> [!WARNING]
> Inherited from Android: when the program has *not* enabled DECSET 2004, `\n`
> becomes `\r`. A multi-line clipboard then executes as a sequence of commands
> the moment Paste is tapped, with no confirmation. Desktop terminals (iTerm2,
> GNOME Terminal, Windows Terminal) prompt in that case. Adding a prompt would be
> a parity decision for all three clients at once.

## Later wizard, pairing and composer features

### Auto Scan launch (CLA-176)

The create-session wizard has a fifth **Auto Scan** mode card, between
Antigravity and Terminal.

- It is enabled only when the selected stored project carries a Linear API key
  and the Claude CLI is installed; otherwise it is greyed out with the reason. A
  custom path has no stored project, so it can never satisfy it.
- The config step has the Claude model / effort pickers (shared with the Claude
  Code step), Plan mode, a Focus view toggle (default ON; sets
  `claude_options.focus_mode`), Skip permissions (default ON for unattended
  scans, with an always-visible caption explaining the off-state), a collapsible
  Advanced section (`claudeAdvancedOptions`), and an Initial prompt seeded from
  the project's saved `auto_scan_prompt` and then the agent-wide default.
- Submit POSTs `/api/autoscan/start` with `worktree` forced true, because the
  scanner branches a worktree per Linear issue, then routes to the CLA-103 live
  monitor. Last-used choices persist to their own `last_used_auto_scan_options`
  bucket, separate from the Claude Code bucket.
- iOS-first (Android mirrors tracked in CLA-223): a scan is one-per-project, so a
  card for a project with a running scan reads "Already running…" and opens the
  live monitor. A start that loses that race is handled as typed `alreadyRunning`
  / `capacity` (409 / 429) failures. Linear-keyed rows on the Projects screen have
  a "Start Auto Scan…" long-press action, via
  `Route.createAutoScan(projectName:)`.

### Device-code pairing (CLA-199)

A "Pair with a code" action in the Agents screen's overflow menu opens a guarded
mint → wait → confirm flow (`DeviceCodePairingState`), beside the CLA-102 QR
scanner. The account-level `trust_key_fp` pin behind it is documented in
[docs/qr-pairing.md](qr-pairing.md).

### Recent custom paths

The wizard's "Custom path…" step loads a "Recent paths" section from
`PreferencesStore` (`getRecentCustomPaths`/`addRecentCustomPath`, key
`recent_custom_paths::<agentKey>`, MRU, cap 5 per agent). Tapping a row fills the
field; swipe or long-press removes it. Both clients trim the path (and one
trailing separator, never a root) before storing it. A path is recorded only
after the session is actually created, and never becomes the last-used project.

#### Sign-out purge (CLA-258)

Signing out drops every agent's bucket: `SessionManager.invalidateUserSession`
calls `PreferencesStore.removeAllRecentCustomPaths()`, which sweeps the
`recent_custom_paths::` prefix through `removeAll(withPrefix:)` (also used by
`removeAllAttentionSeen()`). The hook is the session teardown itself, so the
purge does not depend on the notifications subsystem and covers account deletion
(WS 4010 routes to the same method). `agentDisconnect()` must not purge, since
per-agent recents survive an agent switch. For the same reason the token store is
the wrong hook: it is cleared on both paths.

#### Plaintext scope key (CLA-258 item 6)

The scope key stays plaintext in the key name, deliberately. In DIRECT mode there
is no `agent_id`, so `TokenStore.getAgentScopeKey()` falls back to the agent URL,
and an internal hostname becomes part of a preference key
(`recent_custom_paths::https://build-box.example.internal:8765`). Hashing it
would protect nothing, because the *values* in the same unencrypted bucket are
absolute workstation paths. It would also break the key format all three clients
share and orphan every existing bucket. The sign-out purge removes the key
outright, name included.

### Full-screen composer

An **Expand editor** button (Android `FullScreenInputSheet.kt` parity) is gated
on `TerminalSessionModel.isComposerActive`, not the send gate, since an empty
buffer is legitimate to expand into.

- `FullScreenInputSheet` is a `.sheet` over the same `inputText` the inline
  composer binds to, so expanding and collapsing move no text and any dismissal
  route preserves the draft. The cursor/selection position is not preserved
  (no `TextEditor` selection binding before iOS 18; the deployment target is
  17.0).
- The only submit path is `submitComposer()`, shared by both composers. It reads
  the trimmed `composerPayload`, so `canSubmitComposer` never offers a Send that
  the tap would refuse.
- `TerminalScreen` suspends `TerminalKeyboardObserver` while the sheet is
  presented (`keyboard.stop()`/`start()` on `showFullScreenInput`, gated on
  `screenVisible`). Presenting a `.sheet` does not refire the presenting view's
  `.onAppear`, so the sheet's keyboard would otherwise drive the terminal's
  pan-don't-resize lift.
- `sendInput` and `composerPayload` trim over `unicodeScalars`, not
  `Character`s, because Swift folds `CR LF` into one grapheme equal to neither
  `"\n"` nor `"\r"`. Otherwise a CRLF-line-ending prompt would read as
  single-line, skip the bracketed-paste wrap and submit at the first line break.

## Layout

```
ios/
├── Config/Protocol.xcconfig      # APP_PROTOCOL_VERSION / MIN_SERVER_PROTOCOL_VERSION
│                                 # + #include of Version.xcconfig and optional Local.xcconfig
│                                 #   (gitignored dev overrides; see Config/Local.xcconfig.example)
├── Config/Version.xcconfig       # MARKETING_VERSION / CURRENT_PROJECT_VERSION, bumped by release-please
├── Mobaide.xcodeproj             # hand-authored; objectVersion 70, Xcode 16 synchronized folder groups;
│                                 # SPM: GoogleSignIn-iOS ≥ 9.0, SwiftTerm 1.15.0,
│                                 #   firebase-ios-sdk ≥ 12.18 (product FirebaseAnalytics only)
│                                 #   (+ swift-argument-parser, GoogleAppMeasurement, Google Ads
│                                 #   on-device conversion, GoogleDataTransport, nanopb
│                                 #   transitively; Package.resolved committed)
├── Mobaide/
│   ├── App/                      # entry point, AppConfig, AppContainer (manual DI), AuthStateModel
│   ├── Assets.xcassets/          # AppIcon — single 1024×1024 opaque icon (iOS 17 needs no smaller
│   │                             # sizes); Wordmark — the pixel mark, 1x only (see Theme below)
│   ├── Core/                     # Concurrency, Protocol (TerminalFrame, JSONValue, WireJSON,
│   │                             # ProtocolVersion), Networking, Storage (Keychain)
│   ├── Data/                     # Auth, Events (AgentEventBus, UserEventBus), Local, Review,
│   │                             # Security, Repository, Voice
│   ├── PrivacyInfo.xcprivacy     # App Store privacy manifest (see below)
│   ├── Models/                   # one Swift file per Android dto/*.kt file (wire DTOs)
│   ├── Notifications/            # attention (iOS-09) and APNs push (iOS-10) classes
│   ├── UI/                       # Theme, Navigation, Common, Onboarding, Connections, Sessions,
│   │                             # Terminal, and the feature screens
│   └── Support/                  # Info.plist (protocol keys, CentralServerURL, Google client ids,
│                                 # reversed-client-id URL scheme, UIAppFonts) + Mobaide.entitlements (SIWA)
└── MobaideTests/                 # unit tests + fixture round-trip suites
tests/protocol_fixtures/          # language-neutral golden fixtures (see below)
```

Both `Mobaide/` and `MobaideTests/` are Xcode 16 synchronized folder groups:
files added on disk join the right target automatically — no pbxproj edits.
The test bundle pulls `tests/protocol_fixtures/` in wholesale through a folder
reference, so new fixture files need no project changes either.

## Theme / design system

The app renders in the web client's "modern pixel" design system
([`docs/website.md`](website.md), `web/src/styles/theme.css`), not Android's
Material template. `ios/Mobaide/UI/Theme/` is the **third token mirror**:

- `Theme.swift`'s `MobaideColorScheme.dark` copies `site.css`'s `:root` verbatim.
- `.light` copies `theme.css`'s light block.
- **A token change on the web must be mirrored here**, hex for hex.

Color rules:

- The brand purples (`brand` `#6008AC`, `brand2` `#6650A4`) are `static`
  fill/ring colours only, never text on dark. Text takes `accent`.
- Interactive control boundaries ring in `gray`, decorative frames in `rule`,
  plates in `surface2`.

### Theme source files

| File | What |
|---|---|
| `Theme.swift` | `MobaideColorScheme` (both palettes), `PixelTone` (views resolve it with `colors.color(_:)` / `colors.ring(_:)`), the `\.mobaideColors` environment key, `mobaideTheme()`. |
| `Type.swift` | Departure Mono is the **chrome** face (nav titles, section headings, field labels, chips, small buttons, page titles at 11 / 13 / 18 / 22 / 33 pt); body copy stays the system face — the web's `--font-chrome` / `--font-body` split. Dynamic Type still scales; it degrades to the platform monospace when the font is not registered. |
| `PixelModifiers.swift` | The grammar: `pixelRing` (a 2pt **inset** `strokeBorder`, never `stroke`), `pixelShadow` (an offset `Rectangle`, no blur), `chromeText` (uppercase + 0.08em kerning — visual only, VoiceOver reads the source string), the plates (`pixelCard`, `pixelPanel`, …), list and form modifiers. |
| `PixelButtonStyle.swift` | `.pixel` (ghost), `.pixelPrimary`, `.pixelDanger`, `Small` and `Block` variants. Pressed = `offset(4, 4)` with the shadow hidden and no animation. Applied per site, never globally — toolbar items and `Menu` labels must not pick it up. |
| Other components | `PixelSegmented` replaces every `.pickerStyle(.segmented)` (menu-style `Picker`s stay system). `PixelChip` status is an 8pt square, never a circle. `PixelLoadingBlock` is the only progress indicator (blinking block cursor, 900 ms; lit steady under Reduce Motion). `DotGridBackground` is the page texture; never put it inside list rows. |
| `NavigationBarAppearance.swift` | Installed from `MobaideApp.init()` before the first `NavigationStack` exists. `DEBUG` asserts if the font is missing. |
| `Fonts/` | `DepartureMono-Regular.otf` (v1.500, from the [upstream release](https://github.com/rektdeckard/departure-mono) — the web ships only the woff2, which iOS cannot register) and its OFL, registered through `UIAppFonts` in `Support/Info.plist`. `ChromeFontTests` fails if either half is missing. |

### Brand mark

`Assets.xcassets/Wordmark.imageset` is a byte copy of `web/public/wordmark.png`
at **1x only**. It is pixel art, scaled nearest-neighbour on the `plate`
(`#F4F4F4`) chip in both themes, because the ink is drawn for a light
background.

### Appearance preference

`ThemeStore` (`Data/Local/`) holds the web `ThemePicker`'s three-way preference
under the same key, `mobaide-theme`. Settings › Appearance is a `PixelSegmented`
(Auto / Light / Dark). Unknown stored values coerce to `system`.

### Always-dark surfaces

Always dark, in both themes: the SwiftTerm grid (`MobaideColors.terminal*`:
`#12141A` / `#E6E8EE` / cursor `#4DA3FF`; the 16-colour ANSI table is
fixture-locked and shared with Android and the web) and the auto-scan log pane
(`MobaideColors.log*`).

### Deliberate deviations

All are platform conventions: `NavigationStack` push navigation instead of the
web header's links; system `.alert` / `confirmationDialog`, menu-style `Picker`s
and `Stepper`; vendor Google / Apple sign-in buttons (the app's only rounded
corners). `Form` sections cannot carry a ring, so `pixelForm()` renders them as
square `bg2` rows on the dot grid. `List`s keep their pull-to-refresh and swipe
actions with `pixelList()` + per-row `pixelCard()`.

### Session git Changes and Diff screens

The session-scoped git Changes / Diff screens (terminal `⋯` → Git → Changes…,
`UI/Terminal/git/`) add one more deviation: their whole-screen states are
`PixelEmptyState` plates.

#### Diff layout

The Diff follows Android's layout: a fixed header card over a `LazyVStack` inside
a two-axis `ScrollView`. Every line's text is pinned to the widest measured line
(`GitDiffDisplay.metrics`), so rows scroll sideways together, up to the
2 000-column cut. Gutters and hunk-header rows stay at the leading edge. SwiftUI
has no directional lock, so a diagonal drag moves both axes.

#### Stage, Unstage and Discard

Stage / Unstage / Discard (Discard always behind a confirmation) are per row
(swipe actions and context menu), per section (Stage all / Unstage all), and on
the Diff screen's toolbar. A top-bar Select switches the list into `EditMode`
with a bottom-bar multi-select toolbar. Every action is gated on the matching
`capabilities.git` key (`GitChangesModel.capabilities`).

#### Commit sheet

`⋯ → Git → Commit…` opens the Changes screen with `GitCommitSheet` over it
(`Route.sessionGitChanges(sessionId:commit:)`). It mirrors Android's Summary +
Description, author line, Amend prefill from `commit/context`, and Skip hooks
(`--no-verify`).

#### Fetch, Pull and Push (#676)

Network operations run as agent-side jobs. The menu's `⋯ → Git → Fetch / Pull /
Push…` (`Route.sessionGitChanges(sessionId:commit:sync:)`), the Changes toolbar's
Sync button and the header's tappable `↑n ↓m` line all present `GitSyncSheet`.

- Three rows, each with the disabled reason under it: Fetch (always), Pull (when
  behind), Push (when ahead, or *Publish branch* without an upstream).
- Then the running job's `phase` / `percent` with Cancel, or the outcome with its
  confirmed follow-ups: Pull with rebase / merge after `not_fast_forward` (when
  the branch still has a live upstream), and Force push (with lease) after
  `rejected` or a non-fast-forward push.
- The `confirmationDialog` is attached to the sheet, not the screen, so it can
  present over it. Its `isPresented` setter clears only `syncPrompt` while
  `pendingFollowUp` survives. The Discard, Check out and Rebase dialogs use the
  same split.
- The list shows the job as a banner until its result is dismissed (a success
  clears itself after 6 s).

#### Job tracking

`GitChangesModel.job` is fed by `observeGitJobFrames()` (adopting any job the
socket reports for the session, another client's included) and by
`runJobReconcile()`, a 3 s `GET …/git/jobs/{id}` loop. A `running` snapshot never
overwrites a `done` one. `isBusy` (a mutation or a running job) gates every
staged-set write, because the agent answers `job_running` meanwhile. A coded
`detail` is read before the 404 branches, so `job_not_found` is a lost job rather
than an outdated agent.

#### Watching the session

Both child screens hold a ref-counted watch on `ForegroundSessionTracker`
(`View.holdsSessionWatch`) while on the stack, so the terminal underneath stays
attached and returning never costs a ring replay.

### Project Git screen branch management (#677)

The project Git screen (`UI/Projects/git`) has branch management on top of the
delete flow.

- `load()` always asks for `?remote=true`. `GitScreenModel.loadCapabilities()`
  runs from the screen's own `.task`.
- Local and remote rows render as an A→Z folder tree (a branch name split on `/`;
  a remote row nested under a folder named by its `remote`). Local folders start
  collapsed, remote-name folders expanded. A non-empty filter forces every
  matching folder open.
- The remote-tracking `Section` toggles `remoteExpanded`; a non-empty
  `.searchable` filter, shown at eight rows, forces it open.
- "+" presents `GitNewBranchSheet`: a `Picker` start point defaulting to the
  checked-out branch (`nil` for the agent's `HEAD`), and a checkout `Toggle`.
- Check out is a leading swipe plus a context-menu entry on `branch_only` /
  `remote_branch` rows, behind a `confirmationDialog`.
- `canDelete(item)` allows a `remote_branch` row once
  `capabilities.git.delete_remote` is advertised. A batch that holds one is
  confirmed ("Delete on the remote?") before it leaves edit mode.
- A `branch_with_worktree` row's context menu also offers Remove worktree, which
  drops only the worktree and keeps the branch. Delete on that row reads "Delete
  branch and worktree".

The terminal menu's *Branches…* entry routes to `.gitManagement(projectName:)` for
a project-backed session.

### Rebase in the Changes screen (#678)

The terminal menu's `⋯ → Git → Rebase…` (gated on `capabilities.git.rebase`,
`Route.sessionGitChanges(sessionId:rebase:)`) presents `GitRebaseSheet`.

- A project-backed session picks the target from a `Picker` fed by `getGitItems`.
  Refs are sent fully qualified (`refs/heads/…` / `refs/remotes/…`) so a
  same-named tag cannot shadow them, with deliberately no preselection. Any other
  session, or a list that fails to load, types the ref in a `TextField`.
- While `GET …/git/status` reports `state: rebasing`, the list shows a banner
  above the repo header. With a non-null `repo.rebase` (not a `git am` session,
  not an older agent) it carries Continue (disabled while any conflict remains),
  Skip… and Abort… (the last two behind a `confirmationDialog`). Otherwise it
  points to the terminal.
- A conflict row has a leading swipe action and a context-menu entry, both *Mark
  resolved*: `GitChangesModel.resolve(_:)`, which stages the row. It is gated on
  `capabilities.git.{stage,resolve}` together, because only `resolve` certifies
  that the agent refuses to stage a file still holding `<<<<<<<`/`>>>>>>>`
  markers.

### Conflict resolver screen

With `capabilities.git.conflicts`, tapping a conflict row
(`GitChangesDisplay.opensResolver`) opens `GitConflictScreen` instead of the
`HEAD` diff. It is a `.fullScreenCover(item: $model.conflictTarget)` with its own
`NavigationStack`: a push cannot intercept the swipe-back gesture, and
`.interactiveDismissDisabled(model.isDirty)` needs a cover to enforce.

- The editor is built on `UI/Common/CodeTextView.swift`, a `UIViewRepresentable`
  over `UITextView`, because iOS 17's `TextEditor` cannot disable smart
  quotes/dashes, which would corrupt source code.
- The screen offers a "Which side?" toggle, a conflicts-left count with Next,
  Apply & mark resolved / Save draft, and a `Menu` for whole-file picks, Start
  over… and View diff. Its confirmations are `.alert`, not
  `.confirmationDialog`.
- Closing the cover shows a result toast on the Changes screen. The Changes
  screen also has a *Resolved* section from `GitStatusResponse.resolved_paths`
  (optional on iOS), with a *Restore conflict…* action behind its own `.alert`.

## Device support

`TARGETED_DEVICE_FAMILY = "1"` — iPhone only, on both the app and the test bundle
(a family mismatch against `TEST_HOST` can fail simulator installs).

**Why iPhone only**

- Declaring iPad would oblige 13" iPad screenshots in App Store Connect *and* an
  app that genuinely works on iPad. Otherwise the submission takes a Guideline
  2.1 rejection.
- There is no iPad layout here: no `NavigationSplitView`, and no size-class or
  `UIUserInterfaceIdiom` branches anywhere. So iPhone-only is the accurate
  declaration.

iPad users can still install the app and run it in iPhone-compatibility mode.
`TerminalKeyboardPan` and `TerminalKeyboardObserver` keep their undocked-keyboard
and Split View / Stage Manager geometry handling for that reason. It is
idiom-agnostic math, not iPad support.

**Mac and Vision Pro.** `SUPPORTS_MAC_DESIGNED_FOR_IPHONE_IPAD` and
`SUPPORTS_XR_DESIGNED_FOR_IPHONE_IPAD` are `NO` (they default to **YES**). So the
binary is never auto-published to the Mac App Store or Vision Pro, platforms
nothing in this repo builds or tests against. `SUPPORTS_MACCATALYST = NO`
restates a default for the same reason.

> [!WARNING]
> Widening any of these back is the *last* step of building real iPad or Mac
> support and shooting its screenshots. It is never a standalone edit. It is also
> never an edit to `MobaideTests/DeviceFamilyTests.swift`, which asserts
> `UIDeviceFamily` and the absence of the iPad orientation key against the built
> bundle.

The bundle carries a stray, inert `CFBundleIcons~ipad` (`actool` expands the
universal 1024×1024 icon into an `~ipad` slot). App Store Connect reads
`UIDeviceFamily` for device support.

## Privacy manifest

`Mobaide/PrivacyInfo.xcprivacy` is copied to the bundle root by the synchronized
folder group and is **required for upload**. App Store Connect rejects binaries
(ITMS-91053) that touch a Required Reason API without declaring it.

### Required Reason APIs

The app declares two.

| Category | Reason | Used by |
|---|---|---|
| `NSPrivacyAccessedAPICategoryUserDefaults` | `CA92.1` (defaults readable only by this app) | `PreferencesStore` and `TrustKeyPinning`. |
| `NSPrivacyAccessedAPICategorySystemBootTime` | `35F9.1` (measuring the time elapsed between events inside the app) | `ProcessInfo.systemUptime`, read by `TerminalHostView`, `TerminalCursorReveal` and the review prompt's `HealthyTimeMeter` (see [docs/in-app-review.md](in-app-review.md)). |

`CA92.1` is correct only because both users employ plain custom suites. Adding an
App Group entitlement would make `1C8F.1` the right reason.

When adding an API from any Required Reason category (file timestamps, disk
space, system boot time, active keyboard), add its declaration here too.

The bundled SDKs ship their own manifests (GoogleSignIn, Firebase Analytics /
GoogleAppMeasurement and their transitive dependencies). App Store Connect
aggregates them with the app's. SwiftTerm uses no Required Reason API and needs
none.

### Collected data types

`NSPrivacyCollectedDataTypes` lists six types, non-tracking throughout
(`NSPrivacyTracking` is `false`, no tracking domains).

| Data type | Linked | Purposes |
|---|---|---|
| Email address | Linked | App functionality. |
| User ID | Linked | App functionality. |
| Device ID | Linked | App functionality plus analytics and developer's advertising. Google Analytics for Firebase keys its events by a Firebase app-instance id; IDFV collection is switched off. |
| Audio data | Linked | App functionality. The dictated speech of voice input, relayed to the user's own agent and never stored; see docs/voice-input.md. |
| Product interaction | Not linked | Analytics and developer's advertising. The SDK's automatic app-open / session events and the single `agent_paired` conversion. |
| Coarse location | Not linked | Analytics. The location is derived by Google from the request IP. |

The manifest must stay consistent with the App Privacy answers in App Store
Connect and with the privacy policy's Sections 3.4 and 3.8.
docs/app-conversion-measurement.md carries the App Privacy answers.

Terminal I/O is relayed in real time and not retained server-side, so it is not
"collected" under Apple's definition. Dictation audio is declared anyway, as the
conservative choice.

`MobaideTests/PrivacyManifestTests.swift` asserts all of the above against the
built bundle.

### Firebase Analytics (ad-conversion measurement)

The app links exactly one Firebase product, `FirebaseAnalytics`, for the
once-per-account `agent_paired` conversion described in
docs/app-conversion-measurement.md.

- `FirebaseAnalyticsIdentitySupport` (IDFA) is never added, `AdSupport` is not
  linked, and there is no `NSUserTrackingUsageDescription`, so no App Tracking
  Transparency prompt exists. iOS App campaigns attribute through SKAdNetwork
  (`SKAdNetworkItems` carries Google's `cstr6suwn9.skadnetwork`).
- `OTHER_LDFLAGS` carries `-ObjC` for the Analytics binary.
- `MobaideApp.init()` calls `FirebaseApp.configure(options:)` only when
  `GoogleService-Info.plist` is in the bundle. The file is gitignored and absent
  in worktrees and CI, where the `FirebaseAnalyticsSink` reports itself disabled
  and the conversion component performs no I/O.
- A Release-only run-script phase ("Require Firebase config for Release", first
  in the app target's build phases) fails the build when the plist is missing, so
  an App Store archive cannot ship with analytics inert.
- `Info.plist` fixes the consent-mode defaults (`GOOGLE_ANALYTICS_DEFAULT_ALLOW_*`:
  analytics and ad storage granted, ad user data `eu_consent_policy`, ad
  personalisation denied) and switches automatic screen reporting, IDFV
  collection and the app-delegate proxy off (the app owns its APNs delegate).
  `MobaideTests/FirebaseConfigurationTests.swift` pins all of them.

## Export compliance

`Info.plist` declares `ITSAppUsesNonExemptEncryption = false`. Without the key,
App Store Connect parks every upload behind the manual export-compliance
questionnaire before it can reach TestFlight. That is fine once, but fatal for
unattended release automation.

**Why `false` is accurate.** It is the accurate answer, not a shortcut: the app's
only cryptography is exempt.

- `SignInContract` hashes the sign-in nonce with CryptoKit `SHA256` (a hash, not
  encryption).
- Transport security is OS-provided TLS through `URLSession`/`NWConnection`.
- Secrets sit in the Keychain.
- The bundled SDKs add nothing non-exempt. GoogleSignIn/AppAuth use the same OS
  TLS plus a SHA-256 PKCE challenge. SwiftTerm has no crypto at all.

Apple's exemption for encryption limited to OS-provided facilities and standard
hashing covers all of it, so no annual self-classification report is owed.

> [!WARNING]
> This holds only while that stays true. Encrypting terminal traffic beyond TLS,
> adding client-side data encryption, or vendoring a crypto library makes the
> declaration false. It then requires a real classification (and likely a CCATS
> or self-classification report) before the next upload.

`MobaideTests/ExportComplianceTests.swift` asserts the key against the built
bundle.

## Build / test

```sh
# unit tests (the CLA-97 acceptance command; any available simulator works)
xcodebuild -project ios/Mobaide.xcodeproj -scheme Mobaide \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  CODE_SIGNING_ALLOWED=NO test
```

### Simulator setup

1. First-time setup: `xcodebuild -downloadPlatform iOS` (one-time, ~8 GB), then
   `xcrun simctl list devices available`.
2. Create the device if it is missing: `xcrun simctl create 'iPhone 16'
   com.apple.CoreSimulator.SimDeviceType.iPhone-16`.
3. If the first run fails with "Application failed preflight checks", pre-boot
   and retry: `xcrun simctl boot <udid> && xcrun simctl bootstatus <udid> -b`.

### Local relay

To run the app against a local relay instead of production, use the manual E2E
stack.

- `python tests/e2e/manual_stack.py up` writes (and reverts) the
  `CENTRAL_SERVER_URL` line in `Config/Local.xcconfig` for you.
- `… ios --install-app` builds and installs onto the booted simulator.
- It defaults to loopback because the Info.plist ships no ATS exception. A real
  device over Wi-Fi would fail every REST call while its WebSockets kept working.
- See [manual-e2e-testing.md](manual-e2e-testing.md).

### APNs sandbox testing (iOS-10)

The simulator has no real APNs, but the
notification-handling path (banner, collapse-by-session, tap deep link) can be
exercised with a payload file — `xcrun simctl push <udid> com.mobaide.app
payload.apns` where `payload.apns` contains
`{"aps": {"alert": {"title": "Your move", "body": "proj finished its turn — tap
to continue"}, "sound": "default", "thread-id": "s1"}, "session_id": "s1",
"agent_id": "a1"}` (the `Simulator Target Bundle` key or the bundle-id argument
selects the app).

End-to-end delivery needs a real device plus a server configured with
`APNS_KEY_PATH` / `APNS_KEY_ID` / `APNS_TEAM_ID` / `APNS_TOPIC`, and
`APNS_USE_SANDBOX=true` for Xcode-installed builds (`central_server/.env.example`).

1. Run the app once signed in. The token POST logs
   `device.push_token_registered` server-side.
2. Suspend or force-quit the app.
3. Trigger attention on a session. The alert should arrive within seconds.
4. Tap it. It must open the session's terminal.

### CI

`.github/workflows/ios-build.yml` runs the same command on `macos-latest`.

- It selects the newest installed Xcode 26.x at runtime (the runner pool mixes
  macos-15 and macos-26 image snapshots whose 26.x minors differ; the major is
  pinned and the job fails loudly if 26.x disappears), and creates the iPhone 16
  simulator device when the image doesn't pre-create it.
- The job passes `-onlyUsePackageVersionsFromResolvedFile`, so the committed
  `Package.resolved` pins the SPM versions and drift fails loudly.
- The pbxproj stays at `objectVersion = 70` (the minimum for synchronized folder
  groups), so any Xcode ≥ 16 can still open the project.
- `protocol-integrity.yml` additionally asserts the xcconfig protocol constants
  match Android's `build.gradle.kts` values.

## Sign-in configuration

The repo ships with no OAuth secrets. Unconfigured builds show a "not configured"
message on the Google button (Android parity). Sign in with Apple needs no
client-side config beyond the entitlement.

- **Local overrides** live in `ios/Config/Local.xcconfig` (gitignored,
  `#include?`-d by `Protocol.xcconfig`; template:
  `ios/Config/Local.xcconfig.example`). The keys are:
  - `CENTRAL_SERVER_URL`
  - `GOOGLE_IOS_CLIENT_ID`
  - `GOOGLE_SERVER_CLIENT_ID`
  - `GOOGLE_REVERSED_CLIENT_ID` (the URL scheme for the sign-in round trip)

  Rules for these values:
  - `GOOGLE_SERVER_CLIENT_ID` must equal the server's `GOOGLE_CLIENT_ID`. It
    becomes the ID token's `aud`.
  - Every URL value needs the `/$()/` slash escape (xcconfig reads `//` as a
    comment even inside a value).
  - `tests/e2e/manual_stack.py ios` merges `CENTRAL_SERVER_URL` in and out of
    this file without disturbing the Google ids.
- **Apple, server side**: set `APPLE_CLIENT_ID` (the iOS bundle id,
  `com.mobaide.app`) on `central_server` to enable `POST /api/auth/apple`. The
  endpoint verifies the identity token against Apple's JWKS, checks the client
  nonce embedded verbatim in the token, and provisions users through the same
  find-or-create/link-by-verified-email path as Google (`users.apple_sub`,
  migration `0012` / user_version 14). Account-deletion re-auth accepts
  `method="apple"`.
  - Since CLA-198 the audience check accepts a **list**: `APPLE_CLIENT_ID`
    alongside `APPLE_WEB_CLIENT_ID`, the Services ID browser tokens carry. Both
    resolve to the same account because the Services ID is grouped under the
    bundle id as its primary App ID, which fixes Apple's `sub`.
  - Setting only the web one breaks iOS sign-in; see
    [docs/deployment.md](deployment.md).
- **Nonce scheme (all three clients)**: `AuthNonce.generateNonce()` (SHA-256 hex
  of 32 CSPRNG bytes) is embedded verbatim in the provider ID token. For Apple
  the client also POSTs it, so the server can verify the match plus `iat`
  freshness. There is no second hash anywhere in the scheme, in the browser too.

## Protocol-parity approach

Android (`android/app/src/main/java/com/mobaide/app/data/network/`) is the
reference implementation. The Swift port mirrors it structurally, so drift is
reviewable file by file:

- **Wire keys are Swift property names verbatim** (snake_case, no CodingKeys, no
  key strategy) — the same rule the Kotlin DTOs use.
- **kotlinx parity**:
  - Unknown JSON keys are ignored (`JSONDecoder` default).
  - Every Kotlin-defaulted field decodes via `decodeIfPresent ?? default` in a
    hand-written `init(from:)`. kotlinx fills defaults for missing keys, and
    Swift synthesis would throw.
  - `SessionKind` preserves unknown raw values verbatim as `.custom` (proto-v7
    `SessionKindSerializer` parity; an explicit-null `kind` still falls back to
    `.claude`).
  - `ProjectUpdate` relies on synthesized `encodeIfPresent` so nil fields vanish
    (three-state PATCH), while `ProjectWrite.default_flags` is non-optional and
    always serialized.
- **Terminal frames** (`TerminalFrame.swift`): type byte + payload; encode
  `i`/`x`/`m`/`r`, decode `o`/`s`/`b`/`e`, nil for anything else; resize is
  `[0x72, rows>>8, rows&0xFF, cols>>8, cols&0xFF]`.
- **WebSocketCore** mirrors `WebSocketConnection`:
  - 1 s→30 s doubling backoff (reset on open and forceReconnect).
  - A generation counter that drops events from superseded sockets.
  - Terminal close codes 4001/4010/4002 (HTTP 401 at upgrade ≡ 4001).
  - 4011/4012 deliberately fall through to reconnect.
- **RelayRouting** ports `mapToRelayPath` and the URL assembly, including the raw
  (never percent-encoded) `?token=`/`&token=` append and the
  `{relay}/proxy/{agentId}` REST base.

### Known deliberate deviations from Android

Known deliberate deviations from Android's bytes (server-verified safe):

- **Defaulted request fields.** Swift always encodes non-optional defaulted
  request fields (e.g. `rows: 24`) where kotlinx omits values equal to their
  defaults. The server defaults are identical.
- **Integral floats.** `JSONValue` re-encodes `1.0` as `1`. Avoid `x.0` literals
  in fixtures; use fractional values to exercise doubles.
- **Rejected WS upgrades.** Network.framework cannot surface the HTTP status of a
  rejected WS upgrade, so upgrade-time rejections surface as generic transport
  failures (reconnect path) rather than `tokenRejected`. This matches Android in
  practice (OkHttp's 401-only check falls through to reconnect too); terminal
  auth handling lives in the token-refreshing URL provider on both platforms. The
  401≡tokenRejected mapping stays in `WebSocketCore` for transports that do
  surface a real 401.
- **Auth skip-list matching.** `AgentAPI` matches the percent-decoded URL path
  where Android matches the encoded one. It is reachable only via a project name
  embedding an encoded auth suffix (e.g. `x%2Fapi%2Fauth%2Flogin`) and fails
  closed (Bearer withheld → 401). Fix if it ever matters:
  `url.path(percentEncoded: true)`.
- **Unbounded events stream.** `WebSocketCore.events` is an unbounded
  `AsyncStream` where Android bounds its flow at 101 events with drop-newest.
  Attach exactly one prompt consumer to `events` before `connect()`. Each event
  bus (CLA-98) and `TerminalSessionModel` (CLA-100) is the sole,
  promptly-draining consumer of a fresh core per connection cycle.

## Golden fixtures (`tests/protocol_fixtures/`)

Language-neutral goldens every later iOS issue tests against. A fixture edit has
to satisfy every arm that reads it.

**Consumers**

- **The web client is a second consumer since WEB-04.** `web/src/protocol/` reads
  the same JSON off disk under Vitest, and `protocol-integrity` runs that suite
  on every PR.
- **Android is a third since WEB-06.** It reads `keys/` via
  `KeyChordFixtureTest` and the `dto/` tree via `GitChangesDtoFixtureTest` (a
  bidirectional registry gate like iOS's is still CLA-235). Both load through
  `android/app/src/test/kotlin/…/testing/ProtocolFixtures.kt`, which walks up from
  the Gradle working directory to find the repo root.

**Fixture files**

- `dto/{agent,central}/<TypeName>.json` — one file per DTO type: `cases[]` of
  `{name, wire, decoded_canonical?}`. Tests decode `wire`, re-encode, and compare
  JSON trees (key-order-insensitive, null-vs-absent-sensitive) against
  `decoded_canonical ?? wire`. Use `decoded_canonical` when decoding materializes
  defaults or coerces enums.
- `frames/terminal_frames.json` — binary frame samples as hex strings (`encode`
  and `decode` case lists).
- `frames/autoscan_frames.json` — `ws/autoscan` channel JSON frames: `cases[]` of
  `{name, wire, expect}` covering `snapshot` / `caught_up` / `event` (all six
  `ScanProgressPayload` kinds) plus malformed-drop cases, asserted against the
  decoded `AutoScanFrame`.
- `ansi/ansi_sgr.json` — SGR + 256-color ANSI goldens: `cases[]` of
  `{name, input, runs}` mapping a raw log line (ESC as `\u001b`) to the expected
  `AnsiParser` runs, with fg/bg as integer `[r,g,b]` channels.
- `keys/key_chords.json` — the keyboard-chord contract, asserted by all three
  clients (`KeyChordFixtureTests.swift`, `KeyChordFixtureTest.kt`,
  `keyChord.test.ts`): `encode[]` of
  `{chord, token, display, bytes_hex, summary, uses_csi_u, same_as_plain_key}`,
  plus `encode_rejected`, `parse_cases` (modifiers accepted in any order,
  re-emitted canonically) and `parse_rejected`. Android's `KeyChordEncoder.kt` is
  the reference implementation — a disagreement means the fixture is wrong, not
  the Kotlin.
- `relay/relay_mapping.json` — relay path rewrites, WS URL construction, and REST
  base derivation tables.

### Adding a DTO type

Create its fixture JSON and register the type in
`ios/MobaideTests/Fixtures/FixtureRegistry.swift`. The DTO fixture test fails if
either side is missing (bidirectional coverage check). Then classify the new file
in `web/src/protocol/__fixtures__/coverage.ts`. Its own gate fails until the file
is either asserted by a web test or given an owning milestone.

### Cross-arm constraint

One cross-arm constraint: Swift's `Codable` **drops** unknown keys on re-encode,
while the web schemas use Zod's `z.looseObject` and **preserve** them. An
unknown-extra-key round-trip case therefore cannot live in a shared fixture. It
would fail iOS. Web-specific behaviour like that is asserted in TypeScript-only
tests instead.
