import XCTest
@testable import Mobaide

/// The rules the Herdr dashboard reads a snapshot by. Android and web keep the
/// same rules (`HerdrDisplay.kt`, `herdrDisplay.ts`): "seen" is per device and
/// keyed by the pane's `turn`, and the agents that need the user come first.
final class HerdrDisplayTests: XCTestCase {
    private func pane(
        _ id: String = "w1:p1",
        agent: String? = "claude",
        status: String? = nil,
        turn: Int64? = nil
    ) -> HerdrPane {
        HerdrPane(pane_id: id, agent: agent, status: status, turn: turn)
    }

    private func snapshot(_ panes: [HerdrPane], serverId: String = "tui:default") -> HerdrSnapshot {
        HerdrSnapshot(servers: [
            HerdrServer(
                server_id: serverId,
                workspaces: [
                    HerdrWorkspace(
                        workspace_id: "w1",
                        label: "mobaide",
                        tabs: [HerdrTab(tab_id: "w1:t1", label: "main", panes: panes)]
                    ),
                ]
            ),
        ])
    }

    private func rows(_ panes: [HerdrPane], seen: [String: Int64] = [:]) -> [HerdrPaneRow] {
        HerdrDisplay.paneRows(agentId: "agent-1", snapshot: snapshot(panes), seen: seen)
    }

    func testDoneIsShownUntilThisDeviceHasSeenThatTurn() {
        let done = pane(status: "done", turn: 12)

        XCTAssertEqual(HerdrDisplay.status(done, seenTurn: nil), .done)
        XCTAssertEqual(HerdrDisplay.status(done, seenTurn: 11), .done)
        XCTAssertEqual(HerdrDisplay.status(done, seenTurn: 12), .idle)
    }

    func testDoneWithoutATurnIsNeverSeen() {
        // No turn to compare: hiding it would lose a "your move" for good.
        XCTAssertEqual(HerdrDisplay.status(pane(status: "done"), seenTurn: nil), .done)
    }

    func testSeenTurnNeverHidesAnAgentThatNeedsTheUserAgain() {
        XCTAssertEqual(HerdrDisplay.status(pane(status: "blocked", turn: 12), seenTurn: 12), .blocked)
        XCTAssertEqual(HerdrDisplay.status(pane(status: "failed", turn: 12), seenTurn: 12), .failed)
    }

    func testPaneWithoutAnAgentIsAPlainTerminalWhateverItsStatus() {
        XCTAssertEqual(HerdrDisplay.status(pane(agent: nil, status: "blocked"), seenTurn: nil), HerdrStatus.none)
    }

    func testStatusThisBuildDoesNotKnowKeepsTheAgent() {
        XCTAssertEqual(HerdrDisplay.status(pane(status: "hibernating"), seenTurn: nil), .unknown)
        XCTAssertEqual(HerdrDisplay.status(pane(status: nil), seenTurn: nil), .unknown)
    }

    func testRowsReadTheSeenTurnOfThisAgentServerAndPaneOnly() {
        let done = [pane(status: "done", turn: 3)]
        func status(_ key: String) -> HerdrStatus? { rows(done, seen: [key: 3]).first?.status }

        XCTAssertEqual(
            status(HerdrSeenStore.key(agentId: "agent-1", serverId: "tui:default", paneId: "w1:p1")),
            .idle
        )
        // The same pane id on another computer, or on another herdr server of
        // this one, is another pane.
        XCTAssertEqual(
            status(HerdrSeenStore.key(agentId: "agent-2", serverId: "tui:default", paneId: "w1:p1")),
            .done
        )
        XCTAssertEqual(
            status(HerdrSeenStore.key(agentId: "agent-1", serverId: "tui:work", paneId: "w1:p1")),
            .done
        )
    }

    func testSeenKeyDoesNotLetIdsThatShareAPrefixCollide() {
        XCTAssertNotEqual(
            HerdrSeenStore.key(agentId: "a", serverId: "b:c", paneId: "d"),
            HerdrSeenStore.key(agentId: "a:b", serverId: "c", paneId: "d")
        )
        XCTAssertNotEqual(
            HerdrSeenStore.key(agentId: "a", serverId: "b", paneId: "c:d"),
            HerdrSeenStore.key(agentId: "a", serverId: "b:c", paneId: "d")
        )
    }

    func testAgentRowsPutTheAgentsThatNeedTheUserFirstAndLeavePlainTerminalsOut() {
        let all = rows([
            pane("idle", status: "idle"),
            pane("shell", agent: nil),
            pane("working", status: "working"),
            pane("done-1", status: "done", turn: 1),
            pane("blocked", status: "blocked"),
            pane("failed", status: "failed"),
            pane("done-2", status: "done", turn: 1),
        ])

        XCTAssertEqual(
            HerdrDisplay.agentRows(all).map(\.pane.pane_id),
            ["blocked", "failed", "done-1", "done-2", "working", "idle"]
        )
    }

    func testRollupIsTheMostUrgentStatusOfAGroup() {
        XCTAssertEqual(
            HerdrDisplay.rollup(rows([pane("a", status: "idle"), pane("b", status: "working"), pane("c", status: "done")])),
            .done
        )
        XCTAssertEqual(
            HerdrDisplay.rollup(rows([pane("a", status: "done"), pane("b", agent: nil), pane("c", status: "blocked")])),
            .blocked
        )
        XCTAssertEqual(HerdrDisplay.rollup(rows([pane("a", agent: nil)])), HerdrStatus.none)
        XCTAssertEqual(HerdrDisplay.rollup([]), HerdrStatus.none)
    }

    func testRowsAreEmptyBeforeTheFirstSnapshot() {
        XCTAssertEqual(HerdrDisplay.paneRows(agentId: "agent-1", snapshot: nil, seen: [:]), [])
    }
}
