import XCTest
@testable import Mobaide

/// Edge-case coverage for the shared 409-body parser. The model/repository
/// flows (`ProjectsRepositoryTests`, `GitScreenModelTests`) exercise the happy
/// paths; these pin the malformed / cross-shape fallbacks directly.
final class ConflictParsingTests: XCTestCase {
    // MARK: - string(from:) — project conflicts

    func testStringExtractsDetailFromValidBody() {
        XCTAssertEqual(
            ConflictDetail.string(from: #"{"detail":"Project 'p' already exists"}"#),
            "Project 'p' already exists"
        )
    }

    func testStringFallsBackToRawBodyForNonJSON() {
        XCTAssertEqual(ConflictDetail.string(from: "boom"), "boom")
    }

    // MARK: - stringOrNil(from:) — callers with their own fallback message

    func testStringOrNilExtractsDetail() {
        XCTAssertEqual(
            ConflictDetail.stringOrNil(from: #"{"detail":"A custom agent type with this name already exists"}"#),
            "A custom agent type with this name already exists"
        )
    }

    func testStringOrNilReturnsNilForNonDetailShapes() {
        // Unlike string(from:), callers get nil — not the raw body — so they
        // can substitute their own message (e.g. "Couldn't save (HTTP n)").
        XCTAssertNil(ConflictDetail.stringOrNil(from: "boom"))
        XCTAssertNil(ConflictDetail.stringOrNil(from: ""))
        XCTAssertNil(ConflictDetail.stringOrNil(from: #"{"error":"nope"}"#))
        XCTAssertNil(ConflictDetail.stringOrNil(from: #"{"detail":{"busy_by":[]}}"#))
    }

    // MARK: - busyHolders(from:) — git delete conflicts

    func testBusyHoldersParsesNestedHolders() {
        let body = #"""
        {"detail":{"busy_by":[
          {"kind":"session","session_id":"s1","cwd":"/w"},
          {"kind":"project","project_name":"proj","worktree_path":"/wt"}
        ]}}
        """#
        let holders = ConflictDetail.busyHolders(from: body)
        XCTAssertEqual(holders?.count, 2)
        XCTAssertEqual(holders?[0].kind, "session")
        XCTAssertEqual(holders?[0].session_id, "s1")
        XCTAssertEqual(holders?[0].cwd, "/w")
        XCTAssertNil(holders?[0].project_name)
        XCTAssertEqual(holders?[1].kind, "project")
        XCTAssertEqual(holders?[1].project_name, "proj")
        XCTAssertEqual(holders?[1].worktree_path, "/wt")
    }

    func testBusyHoldersReturnsNilForProjectStringDetail() {
        // A project-conflict body (`detail` is a string) is not the busy shape.
        XCTAssertNil(ConflictDetail.busyHolders(from: #"{"detail":"already exists"}"#))
    }
}
