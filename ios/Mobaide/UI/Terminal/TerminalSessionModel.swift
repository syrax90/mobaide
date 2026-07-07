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
/// - `.live` — entered on `replayEnd` (or the first non-marker binary). Replay
///   and live `output` frames are fed identically; only the markers move the
///   machine, so a reconnect re-clears and re-feeds without duplicating output.
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

    private(set) var connectionState: ConnectionState = .reconnecting
    private(set) var sessionStatus: String?
    private(set) var followMode = true
    private(set) var hasUnread = false
    var inputText = ""

    var isConnected: Bool { connectionState == .live || connectionState == .replayingBuffer }
    var isRunning: Bool { sessionStatus == "running" }

    private let webSocketFactory: WebSocketCoreFactory
    private let sessionManager: SessionManager
    private let tokenStore: TokenStore
    private let centralAPI: CentralAPI
    private let userTokenProvider: any UserTokenProviding
    private let foregroundSessionTracker: ForegroundSessionTracker

    private var core: WebSocketCore?
    private var eventTask: Task<Void, Never>?
    private var sendTask: Task<Void, Never>?
    private var resizeTask: Task<Void, Never>?
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
        initialStatus: String?,
        webSocketFactory: WebSocketCoreFactory,
        sessionManager: SessionManager,
        tokenStore: TokenStore,
        centralAPI: CentralAPI,
        userTokenProvider: any UserTokenProviding,
        foregroundSessionTracker: ForegroundSessionTracker
    ) {
        self.sessionId = sessionId
        self.kind = kind
        self.webSocketFactory = webSocketFactory
        self.sessionManager = sessionManager
        self.tokenStore = tokenStore
        self.centralAPI = centralAPI
        self.userTokenProvider = userTokenProvider
        self.foregroundSessionTracker = foregroundSessionTracker
        self.sessionStatus = initialStatus
        (outboundStream, outboundContinuation) = AsyncStream.makeStream(of: Data.self)
        terminalView = MobaideTerminalView(frame: .zero)
        terminalView.configureAppearance()
    }

    func focusRequests() -> AsyncStream<Void> { focusBroadcaster.subscribe(replayLatest: false) }

    func start() {
        guard !started else { return }
        started = true
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
            connectionState = .live
        }
        guard let frame = TerminalInboundFrame.decode(data) else { return }
        switch frame {
        case .output(let bytes):
            guard !bytes.isEmpty else { return }
            terminalView.feed(byteArray: Array(bytes)[...])
            if !followMode { hasUnread = true }
        case .state(let json):
            sessionStatus = Self.parseStatus(json) ?? sessionStatus
        case .replayStart:
            terminalView.feed(byteArray: Self.clearBeforeReplay[...])
            inReplay = true
            setFollowMode(true)
            connectionState = .replayingBuffer
        case .replayEnd:
            if inReplay {
                inReplay = false
                connectionState = .live
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
        enqueue(.input(data))
        setFollowMode(true)
    }

    func sendControlKey(_ bytes: [UInt8]) {
        sendBytes(Data(bytes))
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

    /// Wire resize notification, debounced 100 ms so a rotation/keyboard burst
    /// collapses to the final size. The native view is already resized by its
    /// layout pass; this only drives the `r` frame.
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

    /// A resize enqueued while the socket was down is dropped by the transport
    /// and then suppressed by `resize`'s dedupe guard, so re-send the last known
    /// size on every successful connect (including the first, whose initial
    /// resize may have been dropped by a failed prior attempt) to keep the PTY
    /// dimensions correct. Re-sending the same size is idempotent.
    private func resendCachedSize() {
        guard lastResizeRows > 0, lastResizeCols > 0 else { return }
        enqueue(.resize(rows: UInt16(clamping: lastResizeRows), cols: UInt16(clamping: lastResizeCols)))
    }

    // MARK: - Follow mode

    func setFollowMode(_ follow: Bool) {
        if follow {
            hasUnread = false
            terminalView.scroll(toPosition: 1.0)
        }
        if followMode != follow {
            followMode = follow
        }
    }

    /// Driven by the SwiftTerm `scrolled` delegate. Position `1.0` is the
    /// bottom of the scrollback (following); anything less means the user
    /// scrolled into history and follow-mode pauses.
    func onScroll(position: Double) {
        if position >= 0.999 {
            if !followMode {
                followMode = true
                hasUnread = false
            }
        } else if followMode {
            followMode = false
        }
    }

    func jumpToBottom() {
        setFollowMode(true)
    }

    // MARK: - Templates / input assist

    func setInput(_ text: String, requestFocus: Bool) {
        inputText = text
        if requestFocus {
            focusBroadcaster.emit(())
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

    // MARK: - Lifecycle

    func markVisible() {
        foregroundSessionTracker.setVisible(sessionId)
        if connectionState != .live, let core {
            Task { await core.forceReconnect() }
        }
    }

    func markHidden() {
        foregroundSessionTracker.clearVisible(sessionId)
    }

    func disconnect() {
        isTornDown = true
        // Reflect teardown in observable state so the screen disables its send
        // controls instead of accepting input into the finished outbound stream.
        connectionState = .closed
        sessionStatus = nil
        eventTask?.cancel()
        sendTask?.cancel()
        resizeTask?.cancel()
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
