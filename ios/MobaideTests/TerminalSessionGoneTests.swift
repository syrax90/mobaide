import XCTest
@testable import Mobaide

/// `TerminalSessionModel.noteSessionSnapshot` — the one-shot decision behind
/// leaving a terminal that was opened for a session that no longer exists.
///
/// Android parity: `TerminalViewModel.sessionEnded`.
@MainActor
final class TerminalSessionGoneTests: XCTestCase {
    private func makeModel(status: String? = nil) -> TerminalSessionModel {
        TerminalTestSupport.model(sessionId: "s1", initialStatus: status)
    }

    func testAbsentFromASyncedListLeavesOnce() {
        let model = makeModel()

        XCTAssertTrue(model.noteSessionSnapshot(isListed: false, synced: true))
        XCTAssertFalse(
            model.noteSessionSnapshot(isListed: false, synced: true),
            "both the notice and the pop are one-shot; the screen keeps feeding snapshots in"
        )
    }

    func testAnUnsyncedListIsNotEvidence() {
        let model = makeModel()

        XCTAssertFalse(
            model.noteSessionSnapshot(isListed: false, synced: false),
            "a list left over from a dropped tunnel still names sessions the agent lost on restart"
        )
    }

    func testASessionThatDiesUnderTheUserKeepsTheScreen() {
        let model = makeModel()

        XCTAssertFalse(model.noteSessionSnapshot(isListed: true, synced: true))
        XCTAssertFalse(
            model.noteSessionSnapshot(isListed: false, synced: true),
            "it was here a moment ago: the output stays readable instead of ejecting the reader"
        )
    }

    func testBeingListedWhileUnsyncedStillCountsAsSeenAlive() {
        let model = makeModel()

        XCTAssertFalse(model.noteSessionSnapshot(isListed: true, synced: false))
        XCTAssertFalse(
            model.noteSessionSnapshot(isListed: false, synced: true),
            "membership is proof on its own — nothing puts a session in the list but the agent"
        )
    }

    func testALiveStateFrameCountsAsSeenAlive() async throws {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(sessionId: "s1", transportFactory: factory)
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        // The agent answering with a live PTY is proof the session exists,
        // independent of when its list snapshot lands.
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        await waitUntil { model.sessionStatus == "running" }
        XCTAssertEqual(model.sessionStatus, "running")

        XCTAssertFalse(model.noteSessionSnapshot(isListed: false, synced: true))
    }

    func testAStoppedStateFrameIsNotProofOfLife() async throws {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(sessionId: "s1", transportFactory: factory)
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)

        // `stopped` is what the agent now answers an attach to an unknown id
        // with, so it must never suppress the bounce.
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"stopped"}"#.utf8)))
        await waitUntil { model.sessionStatus == "stopped" }
        XCTAssertEqual(model.sessionStatus, "stopped")

        XCTAssertTrue(model.noteSessionSnapshot(isListed: false, synced: true))
    }
}
