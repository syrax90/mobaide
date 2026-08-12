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
        policy: @escaping (TimeInterval, Int) -> Bool = { _, bytes in bytes >= 4 }
    ) async throws -> (TerminalSessionModel, FakeWebSocketConnection, FakeWebSocketTransportFactory) {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(transportFactory: factory)
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
}
