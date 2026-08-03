import Foundation
import Observation
import SwiftTerm

/// Owns the wire connection and the SwiftTerm emulator for one session and
/// survives SwiftUI view recreation (it is cached by `TerminalSessionStore`,
/// not stored as `@State`). Mirrors Android `TerminalViewModel`.
///
/// ## Replay state machine
///
/// The agent replays the scrollback over the same `ws/session/{id}` socket on
/// every (re)connect; there is no REST scrollback fetch. Frames arrive as
/// `state('s') → replayStart('b') → output('o')… → replayEnd('e')`, then live
/// `output` frames follow. `connectionState` drives the connecting overlay:
///
/// - `.reconnecting` — initial, and after any disconnect. The first inbound
///   binary of a cycle upgrades out of it (`.live` provisionally).
/// - `.replayingBuffer` — entered on `replayStart`: the screen is cleared with
///   `ESC[H ESC[2J ESC[3J` exactly once per marker, follow-mode is forced, and
///   the overlay shows "Replaying buffer…" until `replayEnd`.
/// - `.live` — entered on `replayEnd`, or provisionally on the first inbound
///   binary of a cycle (the upgrade precedes decoding, so a marker counts),
///   always via `enterLiveState()`, which also re-asserts the cached PTY size.
///   Replay and live `output` frames are fed identically; only the markers move
///   the machine, so a reconnect re-clears and re-feeds without duplicating output.
@MainActor
@Observable
final class TerminalSessionModel {
    enum ConnectionState: Equatable, Sendable {
        case reconnecting
        case replayingBuffer
        case live
        /// Terminal teardown — the session was killed or removed and the model
        /// was evicted; the socket will not reconnect.
        case closed
    }

    /// Android `MAX_UNREAD_DISPLAY`.
    static let maxUnreadRows = 9999

    /// `ESC[H ESC[2J ESC[3J` — cursor home, erase display, erase scrollback.
    /// Fed before applying each replay buffer (Android `CLEAR_BEFORE_REPLAY`).
    static let clearBeforeReplay: [UInt8] = [
        0x1B, 0x5B, 0x48,             // ESC [ H
        0x1B, 0x5B, 0x32, 0x4A,       // ESC [ 2 J
        0x1B, 0x5B, 0x33, 0x4A,       // ESC [ 3 J
    ]

    let sessionId: String
    let kind: SessionKind
    let terminalView: MobaideTerminalView

    /// The session's true kind, or nil until it is known. Unlike `kind` — the
    /// render seed that falls back to `.claude` when the session row was not
    /// loaded yet — this never guesses: saved shortcuts filter on it and saves
    /// send it to the server, so a wrong value would pin a chord under the
    /// wrong kind (Android `resolvedKind` parity).
    private(set) var resolvedKind: SessionKind?

    private(set) var connectionState: ConnectionState = .reconnecting
    private(set) var sessionStatus: String?
    private(set) var followMode = true
    /// Rows the terminal pushed past the bottom while follow mode was paused
    /// (Android `TerminalViewModel.unreadRows`), capped so a runaway producer
    /// cannot grow the badge without bound.
    private(set) var unreadRows = 0
    var hasUnread: Bool { unreadRows > 0 }
    /// Purely user-controlled composer visibility (Android parity): hidden
    /// sticks across TUI episodes and reconnects — nothing in the connection
    /// or replay machinery may resurrect it.
    private(set) var composerVisible = true
    /// Whether the terminal view itself currently holds first responder (the
    /// direct-typing keyboard); fed by the view's responder overrides.
    private(set) var terminalKeyboardActive = false
    var inputText = ""

    /// Per-kind MRU chords, parsed from the device-local store on demand
    /// (empty until `refreshShortcutRecents` first runs — Android parity).
    private(set) var shortcutRecents: [KeyChord] = []
    /// Transient failure surface for terminal actions — shortcut
    /// save/rename/remove/load and refused pastes — rendered as a toast by both
    /// the terminal screen and the picker sheet.
    var terminalMessage: ToastMessage?

    /// Geometry of the live terminal text selection, or nil when there is none.
    /// Fed by the view's selection controller; drives the floating toolbar.
    var selectionUi: TerminalSelectionUi?

    /// Clipboard read seam, so paste is testable without touching the shared
    /// pasteboard (cf. `synchronizedOutputNow`).
    var clipboardReader: () -> String? = { Clipboard.readText() }
    /// All kinds' saved shortcuts, fed by the repository subscription.
    private var allShortcuts: [ShortcutDto] = []

    var isConnected: Bool { connectionState == .live || connectionState == .replayingBuffer }
    var isRunning: Bool { sessionStatus == "running" }
    /// True when terminal-originated bytes will actually reach the PTY — the
    /// `sendTerminalBytes` predicate. The show-keyboard affordance keys off
    /// this same property so the gate and its UI can never drift apart.
    var canSendTerminalBytes: Bool { connectionState == .live && isRunning }

    private let webSocketFactory: WebSocketCoreFactory
    private let sessionManager: SessionManager
    private let tokenStore: TokenStore
    private let centralAPI: CentralAPI
    private let userTokenProvider: any UserTokenProviding
    private let foregroundSessionTracker: ForegroundSessionTracker
    private let preferencesStore: PreferencesStore
    private let shortcutsRepository: ShortcutsRepository
    private let kindResolver: (String) async -> SessionKind?

    private var core: WebSocketCore?
    private var eventTask: Task<Void, Never>?
    private var sendTask: Task<Void, Never>?
    private var resizeTask: Task<Void, Never>?
    private var shortcutsTask: Task<Void, Never>?
    /// Chord tokens with a create still in flight. A second POST for the same
    /// token is dropped while one is pending: the server upsert probes live
    /// rows only, so a late duplicate create landing after an interleaved
    /// delete would silently resurrect the removed chord.
    private var savesInFlight: Set<String> = []
    private var savedShortcutsRefreshInFlight = false
    private var inReplay = false
    private var started = false
    private var isTornDown = false
    private var lastResizeRows = 0
    private var lastResizeCols = 0

    private let outboundStream: AsyncStream<Data>
    private let outboundContinuation: AsyncStream<Data>.Continuation
    private let focusBroadcaster = Broadcaster<Void>(replay: .none)

    init(
        sessionId: String,
        kind: SessionKind,
        resolvedKind: SessionKind?,
        initialStatus: String?,
        webSocketFactory: WebSocketCoreFactory,
        sessionManager: SessionManager,
        tokenStore: TokenStore,
        centralAPI: CentralAPI,
        userTokenProvider: any UserTokenProviding,
        foregroundSessionTracker: ForegroundSessionTracker,
        preferencesStore: PreferencesStore,
        shortcutsRepository: ShortcutsRepository,
        kindResolver: @escaping (String) async -> SessionKind?
    ) {
        self.sessionId = sessionId
        self.kind = kind
        self.resolvedKind = resolvedKind
        self.webSocketFactory = webSocketFactory
        self.sessionManager = sessionManager
        self.tokenStore = tokenStore
        self.centralAPI = centralAPI
        self.userTokenProvider = userTokenProvider
        self.foregroundSessionTracker = foregroundSessionTracker
        self.preferencesStore = preferencesStore
        self.shortcutsRepository = shortcutsRepository
        self.kindResolver = kindResolver
        self.sessionStatus = initialStatus
        (outboundStream, outboundContinuation) = AsyncStream.makeStream(of: Data.self)
        terminalView = MobaideTerminalView(frame: .zero)
        terminalView.configureAppearance()
        terminalView.onFirstResponderChange = { [weak self] active in
            self?.terminalKeyboardActive = active
        }
    }

    func focusRequests() -> AsyncStream<Void> { focusBroadcaster.subscribe(replayLatest: false) }

    func start() {
        guard !started else { return }
        started = true
        // Subscribed for the model's whole life (not lazily on picker open):
        // the control-key row renders saved-shortcut buttons without the
        // picker ever opening, and `shortcut.*` pushes must land there live.
        shortcutsTask = Task { [weak self] in
            guard let stream = self?.shortcutsRepository.shortcuts() else { return }
            for await list in stream {
                self?.allShortcuts = list
            }
        }
        if resolvedKind == nil {
            Task { [weak self] in _ = await self?.resolveSessionKind() }
        }
        Task { await openConnection() }
    }

    private func openConnection() async {
        guard !isTornDown else { return }
        let core = webSocketFactory.make(
            makeURLProvider(),
            { [sessionManager] in await sessionManager.isLoggedIn() }
        )
        self.core = core
        eventTask = Task { [weak self] in
            for await event in core.events {
                self?.handle(event)
            }
        }
        sendTask = Task { [outboundStream] in
            for await frame in outboundStream {
                _ = await core.send(binary: frame)
            }
        }
        await core.connect()
    }

    private func handle(_ event: WebSocketEvent) {
        switch event {
        case .connected:
            resendCachedSize()
        case .binary(let data):
            handleBinary(data)
        case .disconnected:
            connectionState = .reconnecting
        case .error:
            break
        case .message:
            break
        case .tokenRejected, .accountRemoved, .incompatibleServer:
            connectionState = .reconnecting
        }
    }

    private func handleBinary(_ data: Data) {
        if connectionState == .reconnecting {
            enterLiveState()
        }
        guard let frame = TerminalInboundFrame.decode(data) else { return }
        switch frame {
        case .output(let bytes):
            guard !bytes.isEmpty else { return }
            terminalView.applyingEmulatorOutput {
                terminalView.feedTrackingSynchronizedOutput(Array(bytes)[...])
            }
        case .state(let json):
            sessionStatus = Self.parseStatus(json) ?? sessionStatus
        case .replayStart:
            // The transcript the selection's rows name is about to be erased and
            // refilled, so those coordinates would silently come to mean other
            // content — a reconnect mid-selection must not let Copy lift text
            // the user never selected.
            endSelection()
            terminalView.applyingEmulatorOutput {
                terminalView.feedTrackingSynchronizedOutput(Self.clearBeforeReplay[...])
            }
            inReplay = true
            setFollowMode(true)
            connectionState = .replayingBuffer
        case .replayEnd:
            if inReplay {
                inReplay = false
                enterLiveState()
            }
        }
    }

    // MARK: - Sending

    /// `m` submit: `0x01 0x0B` clear-input-line prefix, then the body wrapped in
    /// bracketed paste (`ESC[200~ … ESC[201~`) only when it spans multiple
    /// lines. The server appends the CR as a separate PTY write, so no trailing
    /// CR is added here.
    func sendInput(_ text: String) {
        let body = text.contains("\n") ? "\u{1B}[200~\(text)\u{1B}[201~" : text
        enqueue(.submit("\u{01}\u{0B}" + body))
        setFollowMode(true)
        inputText = ""
    }

    func sendBytes(_ data: Data) {
        // Any user-originated write dismisses a live selection (Android drops
        // it from `onKeyDown` and on every IME commit). Deliberately here and
        // not in `sendTerminalBytes`: emulator auto-replies produced while
        // parsing output take that function's `isApplyingEmulatorOutput` branch
        // and bypass this, so a bare `ESC[6n` cannot kill a selection.
        endSelection()
        enqueue(.input(data))
        setFollowMode(true)
    }

    /// Bytes originating from the terminal view itself — typed keys while it
    /// is first responder, plus SwiftTerm's automatic replies (DA/DECRQM) to
    /// sequences re-parsed from the replay feed. Dropped unless Live AND
    /// running (Android input-listener parity): a keyboard raised on the
    /// terminal surface must not feed a PTY that stopped while the socket
    /// stayed up, and replay-era auto-replies must not reach the live PTY as
    /// stale duplicates. Composer submits and control keys are deliberately
    /// not gated here — their buttons disable instead.
    ///
    /// A reply the emulator generated while parsing inbound output still goes
    /// on the wire, but skips the resume: it is the program talking to itself,
    /// not the user typing, so letting it run `setFollowMode(true)` would let a
    /// bare cursor-position query (`ESC[6n`, which prompt frameworks emit
    /// routinely) yank a paused viewport back to the tail and wipe the unread
    /// tally — the exact hijack follow mode exists to prevent.
    func sendTerminalBytes(_ data: Data) {
        guard canSendTerminalBytes else { return }
        guard !terminalView.isApplyingEmulatorOutput else {
            enqueue(.input(data))
            return
        }
        sendBytes(data)
    }

    func sendControlKey(_ bytes: [UInt8]) {
        sendBytes(Data(bytes))
    }

    // MARK: - Text selection

    func endSelection() {
        terminalView.selectionController.endSelection()
    }

    /// Selection → system clipboard. Marked sensitive, like the OSC 52 sink:
    /// terminal output routinely carries tokens and paths.
    func copySelection() {
        guard let text = terminalView.selectionController.selectedText() else { return }
        Clipboard.copy(text, isSensitive: true)
        endSelection()
    }

    /// Selection → composer, for editing before resend (Android "Send to Input").
    /// Never submits and never touches the clipboard.
    func sendSelectionToInput() {
        guard let text = terminalView.selectionController.selectedText() else { return }
        endSelection()
        setInput(text, requestFocus: true)
    }

    /// Clipboard → PTY, through the one sanitizing encoder.
    ///
    /// Dismisses the selection first (Android calls `stopTextSelectionMode()`
    /// before its paste sink), then gates on Live+running so pasted bytes obey
    /// the same rule as every other terminal-originated write.
    ///
    /// Deliberately not `sendInput`: that builds an `m` submit frame with a
    /// clear-input-line prefix and wraps multi-line bodies unconditionally, so
    /// routing paste through it would double-wrap the bracketed markers and
    /// render literal `200~`/`201~` in Claude Code. Paste is `i` frames only.
    func pasteFromClipboard() {
        endSelection()
        guard canSendTerminalBytes else { return }
        guard let raw = clipboardReader(), !raw.isEmpty else { return }
        guard raw.utf8.count <= TerminalPaste.maxPasteBytes else {
            terminalMessage = ToastMessage(text: "Clipboard is too large to paste")
            return
        }
        let bracketed = terminalView.getTerminal().bracketedPasteMode
        for write in TerminalPaste.writes(for: raw, bracketed: bracketed) {
            sendBytes(Data(write))
        }
    }

    /// Single-tap on a terminal row. The current input row re-submits with a
    /// bare CR (`i` + `\r`); a historical row is executed (`x`: the server
    /// Ctrl+C-clears, types the payload, then sends CR).
    func sendTappedRow(text: String, isCurrentInput: Bool) {
        guard connectionState == .live else { return }
        setFollowMode(true)
        if isCurrentInput {
            enqueue(.input("\r"))
        } else {
            enqueue(.execute(text))
        }
    }

    /// Wire resize notification, debounced 100 ms so a rotation burst
    /// collapses to the final size. The native view is already resized by its
    /// layout pass; this only drives the `r` frame. (Keyboard show/hide never
    /// reaches here — the screen pans instead of resizing.)
    func resize(rows: Int, cols: Int) {
        let r = max(1, rows)
        let c = max(1, cols)
        guard r != lastResizeRows || c != lastResizeCols else { return }
        lastResizeRows = r
        lastResizeCols = c
        let rows16 = UInt16(clamping: r)
        let cols16 = UInt16(clamping: c)
        resizeTask?.cancel()
        resizeTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(100))
            guard !Task.isCancelled else { return }
            self?.enqueue(.resize(rows: rows16, cols: cols16))
        }
    }

    private func enqueue(_ frame: TerminalOutboundFrame) {
        outboundContinuation.yield(frame.encoded)
    }

    /// Single entry point into `.live` (Android `TerminalViewModel.enterLiveState`).
    private func enterLiveState() {
        connectionState = .live
        resendCachedSize()
    }

    /// Re-assert the last measured grid: on `.connected`, and on every entry
    /// into `.live`.
    ///
    /// The PTY is spawned at the create default (24x80) and only a resize frame
    /// corrects it. `.connected` alone cannot carry that guarantee — the model is
    /// constructed and started inside the SwiftUI `View.init`, so on a first open
    /// the socket routinely opens before the terminal has ever been laid out and
    /// this call no-ops on an empty cache. The `sizeChanged` that follows is
    /// debounced 100 ms and dropped silently by the transport if the socket is
    /// down at that moment, after which `resize`'s dedupe suppresses every
    /// identical retry. The replay-end and first-binary entries give the measured
    /// size its second chance, so an agent-spawned session (Auto Scan) cannot be
    /// left at 24 rows with the bottom of a taller device grid blank. Re-sending
    /// the same size is idempotent.
    private func resendCachedSize() {
        guard lastResizeRows > 0, lastResizeCols > 0 else { return }
        enqueue(.resize(rows: UInt16(clamping: lastResizeRows), cols: UInt16(clamping: lastResizeCols)))
    }

    // MARK: - Follow mode

    /// Set follow mode, additionally pulling the viewport back to the tail when
    /// resuming — every caller resumes, but the pause direction stays available
    /// for parity with Android's `setFollowMode(enabled:)`.
    func setFollowMode(_ follow: Bool) {
        applyFollowMode(follow)
        if follow {
            // Resuming follow means leaving the history the selection was taken
            // in, so drop it — and drop it *before* the snap. A live selection
            // also pins the viewport through `holdsAutoScroll`, which would
            // otherwise make `scrollToBottom()` a silent no-op while the badge
            // cleared and the jump-to-bottom button disappeared. This is also
            // what makes composer submits honour the dismiss-on-send contract:
            // `sendInput` never routes through `sendBytes`.
            endSelection()
            // After the suppression is lifted, or the view would refuse its own
            // snap as if it were SwiftTerm's.
            terminalView.scrollToBottom()
        }
    }

    /// Driven by the terminal's scroll view for user-initiated drags only.
    /// Position `1.0` is the tail (following); anything less means the user
    /// scrolled into history and follow mode pauses. Deliberately does not move
    /// the viewport — the user's finger already did.
    func onUserScrolled(position: Double) {
        applyFollowMode(TerminalFollowScroll.isAtBottom(position: position))
    }

    /// One line the terminal pushed past the bottom. Android accrues the same
    /// tally from `emulator.scrollCounter` per screen update; on iOS SwiftTerm
    /// reports *every* scroll through the `scrolled` delegate, and
    /// `TerminalHostView.Coordinator.scrolled` screens those through
    /// `TerminalFollowScroll.pushesRowIntoScrollback` so that, the narrow-margin
    /// arm aside, only the ones that reached the scrollback arrive here.
    func onEmulatorScrolled() {
        guard !followMode, unreadRows < Self.maxUnreadRows else { return }
        unreadRows += 1
    }

    func jumpToBottom() {
        setFollowMode(true)
    }

    /// The sole writer of the follow-mode triple, so the flag, the unread tally
    /// and the view's snap suppression cannot drift apart. Every write is
    /// equality-guarded: the resume path runs on each sent byte, and `@Observable`
    /// notifies on assignment rather than on change.
    private func applyFollowMode(_ follow: Bool) {
        if follow, unreadRows != 0 {
            unreadRows = 0
        }
        terminalView.isAutoScrollSuppressed = !follow
        if followMode != follow {
            followMode = follow
        }
    }

    // MARK: - Composer / terminal keyboard

    /// Reveal a hidden composer and focus its field. Also drops the
    /// terminal-surface keyboard explicitly (a deliberate divergence from
    /// Android, which relies on focus retargeting alone): UIKit's responder
    /// swap is not atomic across a SwiftUI insert, so without the resign a
    /// lost focus request could leave keystrokes routing into the PTY while
    /// the composer looks active.
    func revealComposer() {
        composerVisible = true
        dismissTerminalKeyboard()
        focusBroadcaster.emit(())
    }

    /// Collapse the composer. No focus side effect (Android parity): the
    /// field unmounting dismisses its keyboard naturally.
    func hideComposer() {
        composerVisible = false
    }

    func toggleTerminalKeyboard() {
        if terminalKeyboardActive {
            dismissTerminalKeyboard()
        } else {
            showTerminalKeyboard()
        }
    }

    /// No connectivity guard: with the keyboard down the toggle button
    /// disables unless `canSendTerminalBytes` (while raised it stays enabled
    /// only to dismiss), and `sendTerminalBytes` protects the wire regardless.
    func showTerminalKeyboard() {
        terminalView.keyboardGate = true
        _ = terminalView.becomeFirstResponder()
    }

    func dismissTerminalKeyboard() {
        _ = terminalView.resignFirstResponder()
    }

    /// Reconciles after system-initiated keyboard dismissals that may bypass
    /// the responder overrides (driven from `keyboardWillHideNotification`).
    /// With a hardware keyboard attached the view stays first responder and
    /// the state correctly stays active.
    func syncTerminalKeyboardState() {
        terminalKeyboardActive = terminalView.isFirstResponder
        if !terminalKeyboardActive {
            terminalView.keyboardGate = false
        }
    }

    // MARK: - Templates / input assist

    func setInput(_ text: String, requestFocus: Bool) {
        inputText = text
        if requestFocus {
            revealComposer()
        }
    }

    func applyTemplate(_ template: TemplateDto) {
        setInput(template.body, requestFocus: true)
        Task { [centralAPI, userTokenProvider] in
            guard let token = await userTokenProvider.userToken() else { return }
            _ = try? await centralAPI.touchTemplate(userToken: token, templateId: template.template_id)
        }
    }

    func loadTemplates() async -> [TemplateDto] {
        guard let token = await userTokenProvider.userToken() else { return [] }
        return (try? await centralAPI.listTemplates(userToken: token)) ?? []
    }

    func saveInputAsTemplate(name: String) {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let body = inputText
        guard !trimmedName.isEmpty, !body.isEmpty else { return }
        Task { [centralAPI, userTokenProvider] in
            guard let token = await userTokenProvider.userToken() else { return }
            _ = try? await centralAPI.createTemplate(userToken: token, name: trimmedName, body: body)
        }
    }

    // MARK: - Custom shortcuts

    /// The picker's per-kind saved shortcuts: filtered on the resolved kind
    /// (empty until it is known — a `.claude` guess would flash another kind's
    /// buttons), server order (position, created_at, shortcut_id), unparseable
    /// tokens from newer clients dropped silently.
    var savedShortcutsForKind: [SavedShortcutUi] {
        guard let resolvedKind else { return [] }
        return allShortcuts
            .filter { $0.kind == resolvedKind.wireName }
            .sorted { a, b in
                if a.position != b.position { return a.position < b.position }
                if a.created_at != b.created_at { return a.created_at < b.created_at }
                return a.shortcut_id < b.shortcut_id
            }
            .compactMap { dto in
                guard let chord = KeyChord.parse(dto.chord_token) else { return nil }
                return SavedShortcutUi(
                    id: dto.shortcut_id,
                    chord: chord,
                    name: dto.name.flatMap { name in
                        name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : name
                    },
                    risky: KeyChordEncoder.encode(chord)?.usesCsiU == true
                )
            }
    }

    // MRU buckets are keyed on the wire kind (`custom_<id>` for custom agent
    // types — rename-stable). A deleted type's bucket is deliberately left
    // behind: it is device-local, invisible without a session of that kind,
    // and purging would race the push-driven type cache (Android parity).
    // Both readers and the writer gate on `resolvedKind` — the construction
    // seed can be a `.claude` guess (cold-start deep link), and reading or
    // writing under a guessed bucket would leak recents across kinds.
    // `adoptResolvedKind` re-runs this on resolution, so an open picker
    // self-heals from empty to the right bucket.
    func refreshShortcutRecents() {
        guard let resolvedKind else {
            shortcutRecents = []
            return
        }
        shortcutRecents = preferencesStore
            .getRecentShortcutChords(kindKey: resolvedKind.wireName)
            .compactMap(KeyChord.parse)
    }

    /// Encode and send the chord, then record it in the device-local MRU.
    /// Deliberately not session-gated here — the send surfaces disable in the
    /// UI instead (Android parity). The send happens before the MRU write so
    /// persistence can never interfere with delivery; the write is skipped
    /// while the kind is unresolved (never persist into a guessed bucket —
    /// Android returns after the send there too).
    func sendChord(_ chord: KeyChord) {
        guard let encoding = KeyChordEncoder.encode(chord) else { return }
        sendBytes(Data(encoding.bytes))
        guard let resolvedKind else { return }
        preferencesStore.addRecentShortcutChord(
            kindKey: resolvedKind.wireName,
            token: chord.token
        )
        refreshShortcutRecents()
    }

    func refreshSavedShortcuts() {
        guard !savedShortcutsRefreshInFlight else { return }
        savedShortcutsRefreshInFlight = true
        Task { [weak self] in
            defer { self?.savedShortcutsRefreshInFlight = false }
            do {
                try await self?.shortcutsRepository.refreshShortcuts()
            } catch {
                self?.terminalMessage = ToastMessage(text: "Failed to load saved shortcuts")
            }
        }
    }

    /// Resolve the true session kind: the value seeded at construction, else
    /// the app-scoped session list (live snapshot, then one-shot REST). The
    /// result is published so later callers skip the lookup.
    func resolveSessionKind() async -> SessionKind? {
        if let resolvedKind { return resolvedKind }
        guard let resolved = await kindResolver(sessionId) else { return nil }
        adoptResolvedKind(resolved)
        return resolvedKind
    }

    func adoptResolvedKind(_ kind: SessionKind) {
        if resolvedKind == nil {
            resolvedKind = kind
            // Recents are gated empty while the kind is unresolved; reload so
            // an open picker repopulates from (and sendChord records into)
            // the resolved kind's bucket.
            refreshShortcutRecents()
        }
    }

    /// Pin the chord server-side, always unnamed — naming is a follow-up
    /// rename (Android parity). Success is silent: the pushed/applied row
    /// flipping the Save toggle to "Saved" is the confirmation.
    func saveShortcut(_ chord: KeyChord) {
        let token = chord.token
        guard savesInFlight.insert(token).inserted else { return }
        Task { [weak self] in
            defer { self?.savesInFlight.remove(token) }
            guard let self else { return }
            guard let kind = await self.resolveSessionKind() else {
                self.terminalMessage = ToastMessage(text: "Failed to save shortcut: session type not known yet")
                return
            }
            do {
                try await self.shortcutsRepository.createShortcut(kind: kind.wireName, chordToken: token)
            } catch {
                self.terminalMessage = ToastMessage(text: "Failed to save shortcut")
            }
        }
    }

    func renameShortcut(id: String, name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        Task { [weak self] in
            guard let self else { return }
            do {
                try await self.shortcutsRepository.renameShortcut(shortcutId: id, name: trimmed)
            } catch {
                self.terminalMessage = ToastMessage(text: "Failed to rename shortcut")
            }
        }
    }

    func removeShortcut(id: String) {
        Task { [weak self] in
            guard let self else { return }
            do {
                try await self.shortcutsRepository.deleteShortcut(shortcutId: id)
            } catch let error as APIError where error.code == 403 || error.code == 404 {
                // Already gone server-side (DELETE answers 403 once the row is
                // soft-deleted): prune the cache silently instead of alarming
                // the user about a delete that effectively succeeded.
                await self.shortcutsRepository.applyDeleted(shortcutId: id)
            } catch {
                self.terminalMessage = ToastMessage(text: "Failed to remove shortcut")
            }
        }
    }

    // MARK: - Lifecycle

    func markVisible() {
        foregroundSessionTracker.setVisible(sessionId)
        if connectionState != .live, let core {
            Task { await core.forceReconnect() }
        }
    }

    func markHidden() {
        foregroundSessionTracker.clearVisible(sessionId)
        // Leaving the screen must drop the terminal keyboard (Android
        // dismiss-on-dispose parity) — the retained view would otherwise keep
        // first responder and swallow keys behind whatever screen comes next.
        dismissTerminalKeyboard()
    }

    func disconnect() {
        dismissTerminalKeyboard()
        isTornDown = true
        // Reflect teardown in observable state so the screen disables its send
        // controls instead of accepting input into the finished outbound stream.
        connectionState = .closed
        sessionStatus = nil
        eventTask?.cancel()
        sendTask?.cancel()
        resizeTask?.cancel()
        shortcutsTask?.cancel()
        outboundContinuation.finish()
        if let core {
            Task { await core.disconnect() }
        }
    }

    private static func parseStatus(_ json: String) -> String? {
        guard
            let data = json.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return object["status"] as? String
    }

    private func makeURLProvider() -> @Sendable () async throws -> String? {
        let sessionId = self.sessionId
        let tokenStore = self.tokenStore
        let sessionManager = self.sessionManager
        return {
            guard await sessionManager.isLoggedIn() else { return nil }
            if tokenStore.isAccessTokenExpiringSoon() {
                if await sessionManager.refresh() == .terminal { return nil }
            }
            guard let token = tokenStore.getAccessToken() else { return nil }
            let mode = tokenStore.getConnectionMode()
            let base: String?
            switch mode {
            case .direct:
                base = tokenStore.getAgentUrl()
            case .relay:
                base = tokenStore.getRelayServerUrl()
            }
            guard let base else { return nil }
            return RelayRouting.webSocketURLString(
                baseURL: base,
                path: "ws/session/\(sessionId)",
                mode: mode,
                agentId: tokenStore.getAgentId(),
                token: token
            )
        }
    }
}
