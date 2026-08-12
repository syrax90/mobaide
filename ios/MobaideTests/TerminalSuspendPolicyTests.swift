import XCTest
@testable import Mobaide

/// The unwatched-session suspend policy. Must stay in step with the web
/// client's `terminal/terminalSuspend.ts` and Android's `TerminalSuspend`.
final class TerminalSuspendPolicyTests: XCTestCase {

    func testKeepsAQuietSessionAttachedHoweverLongItGoesUnread() {
        // The whole point of the byte term: an idle session costs nothing to
        // hold, and suspending it would charge a full ring replay to save that
        // nothing.
        XCTAssertFalse(
            TerminalSuspend.shouldSuspend(
                unwatchedSeconds: TerminalSuspend.graceSeconds * 100,
                bytes: TerminalSuspend.recoupBytes - 1
            )
        )
    }

    func testKeepsABusySessionAttachedInsideTheGraceWindow() {
        // The time term: leaving the app for a moment is not worth a replay
        // even on a chatty session.
        XCTAssertFalse(
            TerminalSuspend.shouldSuspend(
                unwatchedSeconds: TerminalSuspend.graceSeconds - 1,
                bytes: TerminalSuspend.recoupBytes * 4
            )
        )
    }

    func testSuspendsOnceTheSessionHasBothGoneUnreadAndSpentARing() {
        XCTAssertTrue(
            TerminalSuspend.shouldSuspend(
                unwatchedSeconds: TerminalSuspend.graceSeconds,
                bytes: TerminalSuspend.recoupBytes
            )
        )
    }

    func testCutsAFirehoseOffWithoutWaitingOutTheGrace() {
        XCTAssertTrue(
            TerminalSuspend.shouldSuspend(unwatchedSeconds: 0, bytes: TerminalSuspend.firehoseBytes)
        )
    }

    func testHoldsItsBoundariesInclusiveOnEveryTerm() {
        // Pinned so a refactor cannot silently turn a `>=` into a `>` and shift
        // the policy by one byte or one second.
        XCTAssertFalse(
            TerminalSuspend.shouldSuspend(
                unwatchedSeconds: TerminalSuspend.graceSeconds,
                bytes: TerminalSuspend.recoupBytes - 1
            )
        )
        XCTAssertTrue(
            TerminalSuspend.shouldSuspend(
                unwatchedSeconds: TerminalSuspend.graceSeconds - 1,
                bytes: TerminalSuspend.firehoseBytes
            )
        )
    }

    func testPricesTheRecoupThresholdAtOneRingAndTheFirehoseAboveIt() {
        // The constants are the policy, and they are shared across three
        // clients. `recoupBytes` below the agent's 4 MiB ring would make every
        // suspend a guaranteed net loss.
        XCTAssertEqual(TerminalSuspend.recoupBytes, 4 * 1024 * 1024)
        XCTAssertEqual(TerminalSuspend.graceSeconds, 60)
        XCTAssertGreaterThan(TerminalSuspend.firehoseBytes, TerminalSuspend.recoupBytes)
    }
}
