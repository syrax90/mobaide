import XCTest
@testable import Mobaide

final class StopSessionConfirmTests: XCTestCase {
    private func row(_ id: String, cwd: String?) -> SessionResponse {
        SessionResponse(
            session_id: id,
            state: "running",
            created_at: "2026-01-01T00:00:00Z",
            pid: nil,
            command: [],
            cwd: cwd,
            title: nil,
            workspace_name: nil,
            kind: .claude,
            attention: nil,
            exit_reason: nil
        )
    }

    func testWorktreeInUseOnlyWhenAnotherSessionSharesTheCwd() {
        let target = row("a", cwd: "/wt/feature-x")

        XCTAssertTrue(target.worktreeInUse(among: [target, row("b", cwd: "/wt/feature-x")]))
        XCTAssertFalse(
            target.worktreeInUse(among: [target, row("c", cwd: "/wt/other")]),
            "the target itself is not another session"
        )
        XCTAssertFalse(row("a", cwd: nil).worktreeInUse(among: [row("a", cwd: nil), row("b", cwd: nil)]))
    }
}
