import XCTest
@testable import Mobaide

/// The unwatched-session governor.
///
/// The policy is injected and fires on a handful of bytes rather than the
/// real 4 MiB — pushing a ring through the emulator per case would test
/// SwiftTerm rather than this.
@MainActor
final class TerminalSessionSuspendTests: XCTestCase {

    private func outputFrame(_ body: String) -> Data {
        Data([0x6F]) + Data(body.utf8)
    }

    private let replayStart = Data([0x62])
    private let replayEnd = Data([0x65])

    /// A watched, connected model: app active and this terminal on screen, the
    /// only state in which the governor is disarmed.
    private func watchedModel(
        policy: @escaping (TimeInterval, Int) -> Bool = { _, bytes in bytes >= 4 },
        tracker: ForegroundSessionTracker = ForegroundSessionTracker()
    ) async throws -> (TerminalSessionModel, FakeWebSocketConnection, FakeWebSocketTransportFactory) {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(transportFactory: factory, foregroundSessionTracker: tracker)
        model.suspendPolicy = policy
        // Marked watched before `start()` builds a core: `markVisible` on a
        // not-yet-live model force-reconnects, which would close the very
        // connection these cases assert on.
        model.markVisible()
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        return (model, connection, factory)
    }

    private func settle() async {
        try? await Task.sleep(for: .milliseconds(30))
    }

    private func waitForState(
        _ model: TerminalSessionModel,
        _ expected: TerminalSessionModel.ConnectionState
    ) async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if model.connectionState == expected { return }
            try? await Task.sleep(for: .milliseconds(5))
        }
    }

    func testWatchedSessionKeepsStreamingHoweverMuchItSpends() async throws {
        let (model, connection, _) = try await watchedModel()

        connection.emit(.binary(outputFrame("a lot of output")))
        await settle()

        XCTAssertEqual(model.connectionState, .live)
        XCTAssertTrue(connection.closeCalls.isEmpty)
    }

    func testBackgroundedSessionThatSpendsNothingKeepsItsSocket() async throws {
        // The byte term, which is what makes leaving the app briefly free: no
        // spend, no suspend, and so no replay to pay for on return.
        let (model, connection, _) = try await watchedModel()
        connection.emit(.binary(outputFrame("hi")))
        await waitForState(model, .live)

        model.setAppActive(false)
        await settle()

        XCTAssertEqual(model.connectionState, .live)
        XCTAssertTrue(connection.closeCalls.isEmpty)
    }

    func testBackgroundedSessionThatSpendsIsSuspended() async throws {
        let (model, connection, _) = try await watchedModel()
        model.setAppActive(false)

        connection.emit(.binary(outputFrame("spending")))
        await waitForState(model, .suspended)

        XCTAssertEqual(model.connectionState, .suspended)
        XCTAssertFalse(model.isConnected)
        XCTAssertFalse(model.canSendTerminalBytes)
    }

    func testOffScreenSessionIsSuspendedEvenWhileTheAppIsActive() async throws {
        // The store is app-scoped, so leaving the terminal screen leaves the
        // model alive. Nobody is reading it, so nobody should pay for it.
        let (model, connection, _) = try await watchedModel()
        model.markHidden()

        connection.emit(.binary(outputFrame("spending")))
        await waitForState(model, .suspended)

        XCTAssertEqual(model.connectionState, .suspended)
    }

    func testOutputReceivedWhileWatchedIsNotChargedToTheBackground() async throws {
        // The counter re-baselines on the transition, or a long-lived session
        // would carry its whole history into the first background and suspend
        // instantly.
        let (model, connection, _) = try await watchedModel()
        connection.emit(.binary(outputFrame("watched output")))
        await waitForState(model, .live)

        model.setAppActive(false)
        await settle()

        XCTAssertEqual(model.connectionState, .live)
    }

    func testReturningToTheForegroundReopensTheSocket() async throws {
        let (model, connection, factory) = try await watchedModel()
        model.setAppActive(false)
        connection.emit(.binary(outputFrame("spending")))
        await waitForState(model, .suspended)

        model.setAppActive(true)

        let reopened = try await factory.connection(2)
        XCTAssertNotNil(reopened)
        XCTAssertEqual(model.connectionState, .reconnecting)
    }

    func testNeverSuspendsInTheMiddleOfAReplay() async throws {
        // Tearing one down wastes bytes already paid for and leaves the overlay
        // pinned on a span whose terminator never arrives.
        let (model, connection, _) = try await watchedModel()
        model.setAppActive(false)

        connection.emit(.binary(replayStart))
        connection.emit(.binary(outputFrame("replayed history")))
        await waitForState(model, .replayingBuffer)

        XCTAssertEqual(model.connectionState, .replayingBuffer)
    }

    func testDoesNotChargeTheReplayItJustPaidFor() async throws {
        // Otherwise a reconnect that lands while still backgrounded pays a full
        // ring, is re-suspended by its own replay, and loops.
        let (model, connection, _) = try await watchedModel(policy: { _, bytes in bytes >= 12 })
        model.setAppActive(false)

        connection.emit(.binary(replayStart))
        connection.emit(.binary(outputFrame("a long replayed span")))
        connection.emit(.binary(replayEnd))
        await waitForState(model, .live)
        connection.emit(.binary(outputFrame("ab")))
        await settle()

        XCTAssertEqual(model.connectionState, .live)
    }

    func testTheShippedPolicyLeavesAModestSpendAlone() async throws {
        // The real thresholds, not the stub: a few hundred bytes is nowhere
        // near a ring.
        let (model, connection, _) = try await watchedModel(policy: {
            TerminalSuspend.shouldSuspend(unwatchedSeconds: $0, bytes: $1)
        })
        model.setAppActive(false)

        connection.emit(.binary(outputFrame(String(repeating: "x", count: 512))))
        await settle()

        XCTAssertEqual(model.connectionState, .live)
    }

    func testTeardownStillWinsOverTheGovernor() async throws {
        // `disconnect()` is the one-way path; a suspended model must not be
        // able to resurrect itself past it.
        let (model, connection, _) = try await watchedModel()
        model.setAppActive(false)
        connection.emit(.binary(outputFrame("spending")))
        await waitForState(model, .suspended)

        model.disconnect()
        model.setAppActive(true)
        await settle()

        XCTAssertEqual(model.connectionState, .closed)
    }

    // MARK: - Switch-edge size re-assert
    //
    // A peer client may have resized the shared PTY while this one sat idle,
    // so `markVisible` and the `setAppActive` rising edge re-assert the cached
    // grid — bypassing `resize`'s dedupe, keeping the debounce, and only over
    // a socket that is already live.

    private func resizeFrames(_ connection: FakeWebSocketConnection) -> [Data] {
        connection.sentBinaries.filter { $0.first == 0x72 }
    }

    private func waitForResizeFrames(
        _ connection: FakeWebSocketConnection,
        atLeast count: Int
    ) async -> [Data] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if resizeFrames(connection).count >= count { return resizeFrames(connection) }
            try? await Task.sleep(for: .milliseconds(5))
        }
        return resizeFrames(connection)
    }

    private let resize40x120 = Data([0x72, 0x00, 0x28, 0x00, 0x78])

    func testMarkVisibleWhileLiveReassertsCachedSizeBypassingDedupe() async throws {
        let (model, connection, _) = try await watchedModel()
        connection.emit(.binary(replayEnd))
        await waitForState(model, .live)
        model.resize(rows: 40, cols: 120)
        let before = await waitForResizeFrames(connection, atLeast: 1).count
        XCTAssertGreaterThanOrEqual(before, 1, "the measured grid reached the wire")

        // Navigation back to a live terminal: the cache equals the frame
        // already on the wire, so `resize`'s dedupe would swallow the re-send —
        // the switch edge must not consult it.
        model.markVisible()

        let frames = await waitForResizeFrames(connection, atLeast: before + 1)
        XCTAssertEqual(frames.count, before + 1)
        XCTAssertEqual(frames.last, resize40x120)
    }

    func testMarkVisibleWhileReconnectingSendsNoSize() async throws {
        let (model, connection, factory) = try await watchedModel()
        connection.emit(.binary(replayEnd))
        await waitForState(model, .live)
        model.resize(rows: 40, cols: 120)
        _ = await waitForResizeFrames(connection, atLeast: 1)

        model.markHidden()
        connection.emit(.binary(outputFrame("spending")))
        await waitForState(model, .suspended)

        // The return resumes the stream first, so the edge reads
        // `.reconnecting` and must defer to the connect-path re-assert
        // instead of double-sending.
        model.markVisible()
        XCTAssertEqual(model.connectionState, .reconnecting)

        let reopened = try await factory.connection(2)
        try? await Task.sleep(for: .milliseconds(250)) // past the 100 ms debounce
        XCTAssertTrue(
            resizeFrames(reopened).isEmpty,
            "nothing may assert a size while the resumed socket is still reconnecting"
        )
    }

    func testAppActiveRisingEdgeWhileLiveAndVisibleReasserts() async throws {
        let (model, connection, _) = try await watchedModel()
        connection.emit(.binary(replayEnd))
        await waitForState(model, .live)
        model.resize(rows: 40, cols: 120)
        let before = await waitForResizeFrames(connection, atLeast: 1).count

        // A background without spend keeps the socket live, so the return is
        // the pure switch edge: no reconnect path will re-assert for it.
        model.setAppActive(false)
        model.setAppActive(true)

        let frames = await waitForResizeFrames(connection, atLeast: before + 1)
        XCTAssertEqual(frames.count, before + 1)
        XCTAssertEqual(frames.last, resize40x120)
    }

    func testAppActiveRepeatWithoutFallDoesNotReassert() async throws {
        let (model, connection, _) = try await watchedModel()
        connection.emit(.binary(replayEnd))
        await waitForState(model, .live)
        model.resize(rows: 40, cols: 120)
        let before = await waitForResizeFrames(connection, atLeast: 1).count

        // Control Center: `.inactive` never reaches the store, and the return
        // re-fires `.active` with no preceding fall — not a client switch.
        model.setAppActive(true)
        try? await Task.sleep(for: .milliseconds(250))

        XCTAssertEqual(resizeFrames(connection).count, before)
    }

    func testAppActiveRisingEdgeOnHiddenModelStaysSilent() async throws {
        // The store fans `setAppActive` out to every retained model; one
        // sitting behind another screen must not speak just because the app
        // woke up.
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(transportFactory: factory)
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        connection.emit(.binary(replayEnd))
        await waitForState(model, .live)
        model.resize(rows: 40, cols: 120)
        let before = await waitForResizeFrames(connection, atLeast: 1).count

        model.setAppActive(false)
        model.setAppActive(true)
        try? await Task.sleep(for: .milliseconds(250))

        XCTAssertEqual(resizeFrames(connection).count, before)
    }

    // MARK: - Child-screen watch holds
    //
    // A session-scoped child screen (git Changes / Diff) hides the terminal
    // exactly like leaving would; its ref-counted hold on the tracker is what
    // tells the governor the user is still inside the session.

    func testHiddenButHeldSessionKeepsStreaming() async throws {
        let tracker = ForegroundSessionTracker()
        let (model, connection, _) = try await watchedModel(tracker: tracker)
        tracker.holdWatch("s1")
        model.markHidden()

        connection.emit(.binary(outputFrame("spending")))
        await settle()

        XCTAssertEqual(model.connectionState, .live)
        XCTAssertTrue(connection.closeCalls.isEmpty)
    }

    func testHoldTakenAfterHidingReWatchesTheSession() async throws {
        // The tracker has no observation channel, so the store re-evaluates
        // the model explicitly; `syncWatchHold` is that re-evaluation.
        let tracker = ForegroundSessionTracker()
        let (model, connection, _) = try await watchedModel(tracker: tracker)
        model.markHidden()
        tracker.holdWatch("s1")
        model.syncWatchHold()

        connection.emit(.binary(outputFrame("spending")))
        await settle()

        XCTAssertEqual(model.connectionState, .live)
    }

    func testReleasingTheHoldReArmsTheUnwatchedClock() async throws {
        let tracker = ForegroundSessionTracker()
        let (model, connection, _) = try await watchedModel(tracker: tracker)
        tracker.holdWatch("s1")
        model.markHidden()
        connection.emit(.binary(outputFrame("spending")))
        await settle()
        XCTAssertEqual(model.connectionState, .live)

        tracker.releaseWatch("s1")
        model.syncWatchHold()
        connection.emit(.binary(outputFrame("spending")))
        await waitForState(model, .suspended)

        XCTAssertEqual(model.connectionState, .suspended)
    }

    func testHeldSessionStillSuspendsWhenTheAppBackgrounds() async throws {
        // The hold stands in for "on screen", never for "app active".
        let tracker = ForegroundSessionTracker()
        let (model, connection, _) = try await watchedModel(tracker: tracker)
        tracker.holdWatch("s1")
        model.markHidden()
        model.setAppActive(false)

        connection.emit(.binary(outputFrame("spending")))
        await waitForState(model, .suspended)

        XCTAssertEqual(model.connectionState, .suspended)
    }

    func testStoreHoldAndReleaseReEvaluateTheRetainedModel() async throws {
        // The screens go through the store, which must both count the hold
        // and wake the model — a model that was never shown is unwatched
        // from `start()`.
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let factory = FakeWebSocketTransportFactory()
        let tracker = ForegroundSessionTracker()
        let preferencesStore = TerminalTestSupport.isolatedPreferencesStore()
        let store = TerminalSessionStore(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore,
            centralAPI: CentralAPI(centralServerURL: "https://central.test", sessionInvalidator: StubSessionInvalidator()),
            userTokenProvider: StubUserTokenProvider(),
            foregroundSessionTracker: tracker,
            preferencesStore: preferencesStore,
            shortcutsRepository: TerminalTestSupport.shortcutsRepository(harness: harness),
            reviewPrompt: TerminalTestSupport.reviewPrompt(
                preferencesStore: preferencesStore,
                foregroundSessionTracker: tracker
            )
        )
        let model = store.model(id: "s1", kind: .claude, resolvedKind: .claude, initialStatus: nil)
        model.suspendPolicy = { _, bytes in bytes >= 4 }
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        store.holdWatch(sessionId: "s1")
        connection.emit(.binary(outputFrame("spending")))
        await settle()
        XCTAssertEqual(model.connectionState, .live)

        store.releaseWatch(sessionId: "s1")
        connection.emit(.binary(outputFrame("spending")))
        await waitForState(model, .suspended)
        XCTAssertEqual(model.connectionState, .suspended)
    }
}
