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

    func testStringReturnsGenericLabelForEmptyBody() {
        XCTAssertEqual(ConflictDetail.string(from: ""), "Conflict")
    }

    func testStringFallsBackToRawBodyWhenDetailKeyMissing() {
        let body = #"{"error":"nope"}"#
        XCTAssertEqual(ConflictDetail.string(from: body), body)
    }

    func testStringFallsBackToRawBodyWhenDetailIsNotAString() {
        // A git busy-shaped body handed to the project parser: `detail` is an
        // object, not a string, so the parser returns the raw body.
        let body = #"{"detail":{"busy_by":[]}}"#
        XCTAssertEqual(ConflictDetail.string(from: body), body)
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

    func testBusyHoldersReturnsEmptyArrayForEmptyBusyBy() {
        // A valid-but-empty busy list is distinct from a malformed body (nil).
        XCTAssertEqual(ConflictDetail.busyHolders(from: #"{"detail":{"busy_by":[]}}"#)?.isEmpty, true)
    }

    func testBusyHoldersReturnsNilForProjectStringDetail() {
        // A project-conflict body (`detail` is a string) is not the busy shape.
        XCTAssertNil(ConflictDetail.busyHolders(from: #"{"detail":"already exists"}"#))
    }

    func testBusyHoldersReturnsNilWhenDetailWrapperMissing() {
        XCTAssertNil(ConflictDetail.busyHolders(from: #"{"busy_by":[{"kind":"session"}]}"#))
    }

    func testBusyHoldersReturnsNilForNonJSONBody() {
        XCTAssertNil(ConflictDetail.busyHolders(from: "boom"))
    }
}
