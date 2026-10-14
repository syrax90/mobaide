import XCTest
@testable import Mobaide

@MainActor
final class TerminalSessionModelTests: XCTestCase {
    private func makeModel(
        id: String = "s1",
        kind: SessionKind = .claude,
        status: String? = nil,
        factory: FakeWebSocketTransportFactory
    ) -> TerminalSessionModel {
        TerminalTestSupport.model(sessionId: id, kind: kind, initialStatus: status, transportFactory: factory)
    }

    private func connectedModel(
        kind: SessionKind = .claude,
        status: String? = nil
    ) async throws -> (TerminalSessionModel, FakeWebSocketConnection) {
        let factory = FakeWebSocketTransportFactory()
        let model = makeModel(kind: kind, status: status, factory: factory)
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        return (model, connection)
    }

    private func waitForBinaries(_ connection: FakeWebSocketConnection, atLeast count: Int) async -> [Data] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if connection.sentBinaries.count >= count { return connection.sentBinaries }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return connection.sentBinaries
    }

    // MARK: - Frame building

    func testSubmitSingleLineHasClearPrefixNoBracketedPaste() async throws {
        let (model, connection) = try await connectedModel()
        model.sendInput("ls")
        let sent = await waitForBinaries(connection, atLeast: 1)
        XCTAssertEqual(sent.first, Data([0x6D, 0x01, 0x0B]) + Data("ls".utf8))
    }

    func testSubmitMultilineWrapsBracketedPaste() async throws {
        let (model, connection) = try await connectedModel()
        model.sendInput("a\nb")
        let sent = await waitForBinaries(connection, atLeast: 1)
        var expected = Data([0x6D, 0x01, 0x0B])
        expected += Data("\u{1B}[200~a\nb\u{1B}[201~".utf8)
        XCTAssertEqual(sent.first, expected)
    }

    func testControlKeySendsInputFrame() async throws {
        let (model, connection) = try await connectedModel()
        model.sendControlKey([0x03])
        let sent = await waitForBinaries(connection, atLeast: 1)
        XCTAssertEqual(sent.first, Data([0x69, 0x03]))
    }

    func testTappedHistoricalRowExecutesWhenLive() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        try await Task.sleep(for: .milliseconds(20))
        model.sendTappedRow(text: "ls -la", isCurrentInput: false)
        let sent = await waitForBinaries(connection, atLeast: 1)
        XCTAssertEqual(sent.first, Data([0x78]) + Data("ls -la".utf8))
    }

    func testTappedCurrentRowSendsCarriageReturn() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x65]))) // replayEnd → live
        try await Task.sleep(for: .milliseconds(20))
        model.sendTappedRow(text: "whatever", isCurrentInput: true)
        let sent = await waitForBinaries(connection, atLeast: 1)
        XCTAssertEqual(sent.first, Data([0x69, 0x0D]))
    }

    func testTappedRowIgnoredWhenNotLive() async throws {
        let factory = FakeWebSocketTransportFactory()
        let model = makeModel(factory: factory)
        model.start()
        let connection = try await factory.connection(1)
        // No inbound binary yet → still .reconnecting.
        model.sendTappedRow(text: "ls", isCurrentInput: false)
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(connection.sentBinaries.isEmpty)
    }

    func testResizeDebounceCollapsesToFinalValue() async throws {
        let (model, connection) = try await connectedModel()
        model.resize(rows: 30, cols: 100)
        model.resize(rows: 40, cols: 120)
        try await Task.sleep(for: .milliseconds(200))
        let resizes = connection.sentBinaries.filter { $0.first == 0x72 }
        XCTAssertFalse(resizes.isEmpty, "the final resize is sent")
        XCTAssertFalse(
            resizes.contains(Data([0x72, 0x00, 0x1E, 0x00, 0x64])), // 30 rows, 100 cols
            "the debounce must drop the intermediate value"
        )
        XCTAssertTrue(
            resizes.allSatisfy { $0 == Data([0x72, 0x00, 0x28, 0x00, 0x78]) }, // 40 rows, 120 cols
            "only the final size is on the wire"
        )
    }

    func testResizeResentOnReconnect() async throws {
        let (model, connection) = try await connectedModel()
        model.resize(rows: 40, cols: 120)
        try await Task.sleep(for: .milliseconds(200))
        let beforeReconnect = connection.sentBinaries.filter { $0.first == 0x72 }.count
        XCTAssertGreaterThanOrEqual(beforeReconnect, 1, "size sent while connected")

        connection.emit(.opened) // reconnect → .connected → resend cached size
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline,
              connection.sentBinaries.filter({ $0.first == 0x72 }).count <= beforeReconnect {
            try await Task.sleep(for: .milliseconds(5))
        }
        let resizes = connection.sentBinaries.filter { $0.first == 0x72 }
        XCTAssertGreaterThan(resizes.count, beforeReconnect, "the cached size is re-sent on reconnect")
        XCTAssertEqual(resizes.last, Data([0x72, 0x00, 0x28, 0x00, 0x78]))
    }

    /// The model is constructed and started inside the SwiftUI `View.init`, so
    /// on a first open the socket routinely connects before the grid has ever
    /// been measured. Nothing may go on the wire then — least of all a 0x0
    /// resize — and the measurement that follows must still reach the PTY.
    func testConnectBeforeAnyResizeSendsNothingAndLaterResizeStillDelivers() async throws {
        let (model, connection) = try await connectedModel()
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertTrue(
            connection.sentBinaries.filter { $0.first == 0x72 }.isEmpty,
            "an unmeasured grid has no size to assert"
        )

        model.resize(rows: 40, cols: 120)
        let sent = await waitForBinaries(connection, atLeast: 1)
        XCTAssertEqual(
            sent.filter { $0.first == 0x72 },
            [Data([0x72, 0x00, 0x28, 0x00, 0x78])],
            "the first measured size still reaches the PTY"
        )
    }

    /// Auto Scan sessions arrive with a long pre-attach replay, so the layout
    /// pass that measures the grid lands mid-replay. If that frame is dropped
    /// while the socket is down, `resize`'s dedupe suppresses every identical
    /// retry, and without a re-assert at `replayEnd` the PTY stays at its 24x80
    /// spawn size for the session's whole life.
    func testReplayEndReassertsSizeDroppedWhileSocketWasDown() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x62]))) // replayStart
        let replaying = await poll { model.connectionState == .replayingBuffer }
        XCTAssertTrue(replaying)

        connection.dropsBinaries = true
        model.resize(rows: 40, cols: 120)
        let dropped = await poll { !connection.droppedBinaries.isEmpty }
        XCTAssertTrue(dropped, "the debounced resize hit a socket that was down")
        connection.dropsBinaries = false

        connection.emit(.binary(Data([0x65]))) // replayEnd
        let live = await poll { model.connectionState == .live }
        XCTAssertTrue(live)
        let sent = await waitForBinaries(connection, atLeast: 1)
        XCTAssertEqual(
            sent.filter { $0.first == 0x72 },
            [Data([0x72, 0x00, 0x28, 0x00, 0x78])],
            "entering live re-asserts the measured grid the dedupe guard would never retry"
        )
    }

    private func resizeCount(_ connection: FakeWebSocketConnection) -> Int {
        connection.sentBinaries.filter { $0.first == 0x72 }.count
    }

    private func emitHandshakeReplay(_ connection: FakeWebSocketConnection) {
        connection.emit(.binary(Data([0x62]))) // replayStart
        connection.emit(.binary(Data([0x6F]) + Data("history".utf8))) // output
        connection.emit(.binary(Data([0x65]))) // replayEnd
    }

    /// A model whose measured grid is on the wire and whose own handshake has
    /// closed: the debounced layout frame, the first-binary upgrade re-assert,
    /// and the own `replayEnd` re-assert.
    private func liveAfterOwnHandshake() async throws -> (TerminalSessionModel, FakeWebSocketConnection) {
        let (model, connection) = try await connectedModel()
        model.resize(rows: 40, cols: 120)
        let measured = await poll { self.resizeCount(connection) == 1 }
        XCTAssertTrue(measured, "the debounced layout frame goes out")

        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        let upgraded = await poll { self.resizeCount(connection) == 2 }
        XCTAssertTrue(upgraded, "the first binary upgrades to live and re-asserts")

        emitHandshakeReplay(connection)
        let reasserted = await poll { self.resizeCount(connection) == 3 }
        XCTAssertTrue(reasserted, "the end of the own handshake re-asserts the grid")
        XCTAssertEqual(model.connectionState, .live)
        return (model, connection)
    }

    func testOwnHandshakeReplayEndReassertsSize() async throws {
        let (_, connection) = try await liveAfterOwnHandshake()
        XCTAssertEqual(
            connection.sentBinaries.filter { $0.first == 0x72 }.last,
            Data([0x72, 0x00, 0x28, 0x00, 0x78])
        )
    }

    /// The relay fans every attach handshake out to all sockets on the session.
    /// A retained model answering another client's replay would resize the PTY
    /// back to its own grid right after that client opened the session.
    func testForeignHandshakeReplayEndSendsNoResize() async throws {
        let (model, connection) = try await liveAfterOwnHandshake()

        emitHandshakeReplay(connection)
        // Sends are queued in order, so once this input frame is out, any
        // re-assert the foreign `replayEnd` produced would already be ahead of it.
        model.sendControlKey([0x03])
        let flushed = await poll { connection.sentBinaries.last == Data([0x69, 0x03]) }
        XCTAssertTrue(flushed)

        XCTAssertEqual(resizeCount(connection), 3, "a foreign replay end is not answered")
        XCTAssertEqual(model.connectionState, .live)
    }

    func testReconnectRearmsReplayEndReassert() async throws {
        let (model, connection) = try await liveAfterOwnHandshake()
        emitHandshakeReplay(connection) // foreign: consumes nothing

        connection.emit(.opened) // reconnect → .connected → resend + re-arm
        let resent = await poll { self.resizeCount(connection) == 4 }
        XCTAssertTrue(resent, "the connect-path re-assert is unchanged")

        emitHandshakeReplay(connection)
        let reasserted = await poll { self.resizeCount(connection) == 5 }
        XCTAssertTrue(reasserted, "the handshake after a reconnect is our own again")
        XCTAssertEqual(model.connectionState, .live)
    }

    /// The same recovery on the other entry into live — the first inbound
    /// binary upgrading out of `.reconnecting`, with no `.connected` in between
    /// to carry the re-assert.
    ///
    /// `.opened` is deliberately never emitted, so no `.connected` event exists
    /// and the first-binary upgrade is structurally the only path that can put
    /// the measured grid on the wire. `WebSocketCore.doConnect` assigns the
    /// connection before starting the transport, so sends still reach the fake.
    func testFirstBinaryUpgradeReassertsSizeDroppedWhileSocketWasDown() async throws {
        let factory = FakeWebSocketTransportFactory()
        let model = makeModel(factory: factory)
        model.start()
        let connection = try await factory.connection(1)
        XCTAssertEqual(model.connectionState, .reconnecting)

        connection.dropsBinaries = true
        model.resize(rows: 40, cols: 120)
        let dropped = await poll { !connection.droppedBinaries.isEmpty }
        XCTAssertTrue(dropped, "the debounced resize hit a socket that was down")
        connection.dropsBinaries = false

        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        let live = await poll { model.connectionState == .live }
        XCTAssertTrue(live)
        let sent = await waitForBinaries(connection, atLeast: 1)
        XCTAssertEqual(
            sent.filter { $0.first == 0x72 },
            [Data([0x72, 0x00, 0x28, 0x00, 0x78])],
            "the first-binary upgrade re-asserts the measured grid"
        )
    }

    func testDisconnectClosesAndDisablesInput() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertTrue(model.isConnected)
        XCTAssertTrue(model.isRunning)

        model.disconnect()
        XCTAssertEqual(model.connectionState, .closed)
        XCTAssertFalse(model.isConnected, "an evicted/closed session must not read as connected")
        XCTAssertFalse(model.isRunning, "an evicted/closed session must not read as running")
    }

    // MARK: - Terminal-originated byte gate (sendTerminalBytes)

    func testTerminalBytesDroppedWhenNotLive() async throws {
        let (model, connection) = try await connectedModel()
        // No inbound binary yet → still .reconnecting.
        XCTAssertEqual(model.connectionState, .reconnecting)
        model.sendTerminalBytes(Data([0x61]))
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(connection.sentBinaries.isEmpty, "typed keys must not reach a not-yet-live socket")
    }

    func testTerminalBytesDroppedDuringReplay() async throws {
        let (model, connection) = try await connectedModel()
        // Running first, so the drop below is attributable to replay alone.
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        connection.emit(.binary(Data([0x62]))) // replayStart
        let replaying = await poll { model.connectionState == .replayingBuffer }
        XCTAssertTrue(replaying)
        XCTAssertTrue(model.isRunning)

        model.sendTerminalBytes(Data([0x61]))
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            connection.sentBinaries.isEmpty,
            "SwiftTerm auto-replies to re-parsed replay sequences must not hit the live PTY as stale duplicates"
        )
    }

    func testTerminalBytesDroppedWhenLiveButNotRunning() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x65]))) // replayEnd → live; no status frame → not running
        let live = await poll { model.connectionState == .live }
        XCTAssertTrue(live)
        XCTAssertFalse(model.isRunning)

        model.sendTerminalBytes(Data([0x61]))
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            connection.sentBinaries.isEmpty,
            "a keyboard raised on the terminal surface must not feed a PTY that stopped while the socket stayed up"
        )
    }

    func testTerminalBytesFlowAsInputFrameWhenLiveAndRunning() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        let ready = await poll { model.connectionState == .live && model.isRunning }
        XCTAssertTrue(ready)

        model.sendTerminalBytes(Data([0x61])) // 'a'
        let sent = await waitForBinaries(connection, atLeast: 1)
        XCTAssertEqual(sent.first, Data([0x69, 0x61]), "typed bytes travel as plain `i` input frames")
    }

    // MARK: - Show-keyboard affordance (canSendTerminalBytes)

    func testCanSendTerminalBytesFalseDuringReplayEvenThoughConnected() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        connection.emit(.binary(Data([0x62]))) // replayStart
        let replaying = await poll { model.connectionState == .replayingBuffer && model.isRunning }
        XCTAssertTrue(replaying)

        XCTAssertTrue(model.isConnected)
        XCTAssertFalse(
            model.canSendTerminalBytes,
            "replay keeps isConnected true, so an isConnected-keyed affordance would raise a keyboard whose keys sendTerminalBytes drops"
        )
    }

    func testCanSendTerminalBytesWithdrawnWhenSessionStopsWhileSocketStaysLive() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        let ready = await poll { model.connectionState == .live && model.isRunning }
        XCTAssertTrue(ready)
        XCTAssertTrue(model.canSendTerminalBytes)

        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"completed"}"#.utf8)))
        let stopped = await poll { !model.isRunning }
        XCTAssertTrue(stopped)

        XCTAssertEqual(model.connectionState, .live, "only the session stopped — the socket stays live")
        XCTAssertFalse(
            model.canSendTerminalBytes,
            "a stopped session behind a live socket must withdraw the show-keyboard affordance"
        )
    }

    // MARK: - Replay state machine

    func testReplaySequenceDrivesConnectionState() async throws {
        let (model, connection) = try await connectedModel()
        XCTAssertEqual(model.connectionState, .reconnecting)

        connection.emit(.binary(Data([0x62]))) // replayStart
        try await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(model.connectionState, .replayingBuffer)

        connection.emit(.binary(Data([0x6F]) + Data("hello".utf8))) // output
        connection.emit(.binary(Data([0x65]))) // replayEnd
        try await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(model.connectionState, .live)
    }

    func testDisconnectReturnsToReconnecting() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x65])))
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(model.connectionState, .live)

        connection.emit(.closed(code: 1006, reason: "drop"))
        try await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(model.connectionState, .reconnecting)
    }

    func testReplayFeedsClearedBufferWithoutDuplicating() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x62])))
        connection.emit(.binary(Data([0x6F]) + Data("X\r\n".utf8)))
        connection.emit(.binary(Data([0x65])))
        try await Task.sleep(for: .milliseconds(40))

        connection.emit(.binary(Data([0x62]))) // reconnect replay clears first
        connection.emit(.binary(Data([0x6F]) + Data("X\r\n".utf8)))
        connection.emit(.binary(Data([0x65])))
        try await Task.sleep(for: .milliseconds(40))

        let firstLine = model.terminalView.getTerminal().getScrollInvariantLine(row: 0)?
            .translateToString(trimRight: true)
            .trimmingCharacters(in: .whitespaces)
        XCTAssertEqual(firstLine, "X", "clear-before-replay must prevent duplicated output")
    }

    // MARK: - Retention

    func testStoreReusesModelInstance() {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let store = TerminalTestSupport.store(harness)
        let first = store.model(id: "s1", kind: .claude, resolvedKind: nil, initialStatus: nil)
        let again = store.model(id: "s1", kind: .claude, resolvedKind: nil, initialStatus: nil)
        XCTAssertTrue(first === again, "same id must return the retained model")

        let other = store.model(id: "s2", kind: .claude, resolvedKind: nil, initialStatus: nil)
        XCTAssertFalse(first === other)

        store.evict("s1")
        let rebuilt = store.model(id: "s1", kind: .claude, resolvedKind: nil, initialStatus: nil)
        XCTAssertFalse(first === rebuilt, "evicted id must build a fresh model")
    }

    func testStoreTopsUpResolvedKindOnCachedModel() {
        // A model created before the session row loaded (cold-start deep link)
        // has no resolved kind; the next lookup that knows better adopts it.
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let store = TerminalTestSupport.store(harness)
        let first = store.model(id: "s1", kind: .claude, resolvedKind: nil, initialStatus: nil)
        XCTAssertNil(first.resolvedKind)

        let again = store.model(id: "s1", kind: .claude, resolvedKind: .codex, initialStatus: nil)
        XCTAssertTrue(first === again)
        XCTAssertEqual(first.resolvedKind, .codex, "cached models adopt a later-known kind")

        _ = store.model(id: "s1", kind: .claude, resolvedKind: .shell, initialStatus: nil)
        XCTAssertEqual(first.resolvedKind, .codex, "an adopted kind is never overwritten")
    }

    // MARK: - Follow mode / unread

    private func poll(_ predicate: () -> Bool) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate() { return true }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return predicate()
    }

    func testScrollingIntoHistoryPausesFollowAndOutputMarksUnread() async throws {
        let (model, connection) = try await connectedModel()
        XCTAssertTrue(model.followMode)
        XCTAssertFalse(model.hasUnread)

        model.onUserScrolled(position: 0.5) // user scrolled up into scrollback
        XCTAssertFalse(model.followMode, "leaving the bottom pauses follow-mode")

        // "late line" carries no newline, so it never scrolls the emulator's
        // buffer — the unread tally is driven by the emulator's own scroll
        // signal (`onEmulatorScrolled`), not by output frames arriving.
        connection.emit(.binary(Data([0x6F]) + Data("late line".utf8))) // output
        model.onEmulatorScrolled() // the terminal pushed a row past the bottom
        XCTAssertTrue(model.hasUnread, "output that scrolls the buffer while paused must raise the unread flag")
        XCTAssertFalse(model.followMode, "incoming output must not silently resume follow-mode")
    }

    func testOutputWhileFollowingNeverMarksUnread() async throws {
        let (model, connection) = try await connectedModel()
        XCTAssertTrue(model.followMode)

        connection.emit(.binary(Data([0x6F]) + Data("live line".utf8))) // output → also flips to .live
        let processed = await poll { model.connectionState == .live }
        XCTAssertTrue(processed, "output frame should have been handled")
        XCTAssertFalse(model.hasUnread, "a following viewer never accrues unread")
    }

    func testScrollBackToBottomResumesFollowAndClearsUnread() async throws {
        let (model, _) = try await connectedModel()
        model.onUserScrolled(position: 0.5)
        model.onEmulatorScrolled() // the terminal pushed a row past the bottom while paused
        XCTAssertTrue(model.hasUnread, "the tally must be genuinely non-zero before the resume")

        model.onUserScrolled(position: 1.0) // returned to the bottom
        XCTAssertTrue(model.followMode)
        XCTAssertFalse(model.hasUnread, "returning to the bottom clears unread")
    }

    func testJumpToBottomResumesFollowAndClearsUnread() async throws {
        let (model, _) = try await connectedModel()
        model.onUserScrolled(position: 0.5)
        model.onEmulatorScrolled() // the terminal pushed a row past the bottom while paused
        XCTAssertTrue(model.hasUnread, "the tally must be genuinely non-zero before the resume")

        model.jumpToBottom()
        XCTAssertTrue(model.followMode)
        XCTAssertFalse(model.hasUnread)
    }

    // MARK: - Unread rows

    /// Follow mode is driven by the view and the emulator, not the socket.
    private func offlineModel() -> TerminalSessionModel {
        makeModel(factory: FakeWebSocketTransportFactory())
    }

    func testEmulatorScrollsAccrueUnreadRowsOnlyWhilePaused() {
        let model = offlineModel()

        model.onEmulatorScrolled()
        XCTAssertEqual(model.unreadRows, 0, "a following viewer is already looking at the row that scrolled by")

        model.onUserScrolled(position: 0.5)
        model.onEmulatorScrolled()
        model.onEmulatorScrolled()
        model.onEmulatorScrolled()

        XCTAssertEqual(
            model.unreadRows, 3,
            "SwiftTerm reports one scrolled line at a time — Android accrues the same tally from its scrollCounter delta"
        )
        XCTAssertTrue(model.hasUnread)
    }

    func testUnreadRowsSaturateAtTheDisplayCap() {
        let model = offlineModel()
        model.onUserScrolled(position: 0.5)

        for _ in 0..<(TerminalSessionModel.maxUnreadRows + 25) {
            model.onEmulatorScrolled()
        }

        XCTAssertEqual(
            model.unreadRows, TerminalSessionModel.maxUnreadRows,
            "a runaway producer must not grow the badge without bound"
        )
    }

    func testScrollingBackToTheTailClearsTheTallyAndLiftsSnapSuppression() {
        let model = offlineModel()
        model.onUserScrolled(position: 0.5)
        model.onEmulatorScrolled()
        XCTAssertTrue(model.terminalView.isAutoScrollSuppressed)

        model.onUserScrolled(position: 1.0)

        XCTAssertEqual(model.unreadRows, 0)
        XCTAssertFalse(
            model.terminalView.isAutoScrollSuppressed,
            "the flag and the view's snap suppression are written together or a resumed viewport stays frozen"
        )
    }

    func testSubmittingInputResumesFollowAndClearsTheTally() async throws {
        let (model, _) = try await connectedModel()
        model.onUserScrolled(position: 0.5)
        model.onEmulatorScrolled()

        model.sendInput("ls")

        XCTAssertTrue(model.followMode, "typing is a resume trigger — the answer has to be visible")
        XCTAssertEqual(model.unreadRows, 0)
    }
}
