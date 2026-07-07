import XCTest
@testable import Mobaide

@MainActor
final class TerminalSessionModelTests: XCTestCase {
    private struct StubInvalidator: SessionInvalidating {
        func invalidateUserSession() async {}
    }

    private struct StubUserToken: UserTokenProviding {
        func userToken() async -> String? { "user-token" }
    }

    private func makeModel(
        id: String = "s1",
        kind: SessionKind = .claude,
        status: String? = nil,
        factory: FakeWebSocketTransportFactory
    ) -> TerminalSessionModel {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        return TerminalSessionModel(
            sessionId: id,
            kind: kind,
            initialStatus: status,
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore,
            centralAPI: CentralAPI(centralServerURL: "https://central.test", sessionInvalidator: StubInvalidator()),
            userTokenProvider: StubUserToken(),
            foregroundSessionTracker: ForegroundSessionTracker()
        )
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
        XCTAssertEqual(resizes.count, 1, "burst must collapse to a single resize frame")
        XCTAssertEqual(resizes.first, Data([0x72, 0x00, 0x28, 0x00, 0x78])) // 40 rows, 120 cols
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
        let store = TerminalSessionStore(
            webSocketFactory: WebSocketCoreFactory.live(
                transportFactory: FakeWebSocketTransportFactory(),
                sleeper: RecordingSleeper()
            ),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore,
            centralAPI: CentralAPI(centralServerURL: "https://central.test", sessionInvalidator: StubInvalidator()),
            userTokenProvider: StubUserToken(),
            foregroundSessionTracker: ForegroundSessionTracker()
        )
        let first = store.model(id: "s1", kind: .claude, initialStatus: nil)
        let again = store.model(id: "s1", kind: .claude, initialStatus: nil)
        XCTAssertTrue(first === again, "same id must return the retained model")

        let other = store.model(id: "s2", kind: .claude, initialStatus: nil)
        XCTAssertFalse(first === other)

        store.evict("s1")
        let rebuilt = store.model(id: "s1", kind: .claude, initialStatus: nil)
        XCTAssertFalse(first === rebuilt, "evicted id must build a fresh model")
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

        model.onScroll(position: 0.5) // user scrolled up into scrollback
        XCTAssertFalse(model.followMode, "leaving the bottom pauses follow-mode")

        connection.emit(.binary(Data([0x6F]) + Data("late line".utf8))) // output
        let unread = await poll { model.hasUnread }
        XCTAssertTrue(unread, "output while paused must raise the unread flag")
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
        let (model, connection) = try await connectedModel()
        model.onScroll(position: 0.5)
        connection.emit(.binary(Data([0x6F]) + Data("x".utf8)))
        _ = await poll { model.hasUnread }

        model.onScroll(position: 1.0) // returned to the bottom
        XCTAssertTrue(model.followMode)
        XCTAssertFalse(model.hasUnread, "returning to the bottom clears unread")
    }

    func testJumpToBottomResumesFollowAndClearsUnread() async throws {
        let (model, connection) = try await connectedModel()
        model.onScroll(position: 0.5)
        connection.emit(.binary(Data([0x6F]) + Data("x".utf8)))
        _ = await poll { model.hasUnread }

        model.jumpToBottom()
        XCTAssertTrue(model.followMode)
        XCTAssertFalse(model.hasUnread)
    }
}
