import XCTest
@testable import Mobaide

final class ScanPayloadParserTests: XCTestCase {
    func testPhaseFullPayload() {
        let payload: [String: Any] = [
            "phase": "scan_started",
            "cycle_number": 3,
            "next_scan_at": "2026-07-08T12:00:00Z",
            "issues_processed": 2,
        ]
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "phase", payload: payload),
            .phase(phase: "scan_started", cycleNumber: 3, nextScanAt: "2026-07-08T12:00:00Z", issuesProcessed: 2)
        )
    }

    func testPhaseMissingFieldsTakeDefaults() {
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "phase", payload: [:]),
            .phase(phase: "", cycleNumber: 0, nextScanAt: nil, issuesProcessed: 0)
        )
    }

    func testIssueWithError() {
        let payload: [String: Any] = [
            "identifier": "ISS-1",
            "title": "Fix bug",
            "step": "state_update",
            "status": "failed",
            "error": "boom",
        ]
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "issue", payload: payload),
            .issue(identifier: "ISS-1", title: "Fix bug", step: "state_update", status: "failed", error: "boom")
        )
    }

    func testIssueWithoutErrorIsNil() {
        let payload: [String: Any] = ["identifier": "ISS-2", "title": "t", "step": "label_removal", "status": "ok"]
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "issue", payload: payload),
            .issue(identifier: "ISS-2", title: "t", step: "label_removal", status: "ok", error: nil)
        )
    }

    func testWorktree() {
        let payload: [String: Any] = ["identifier": "ISS-3", "path": "/w/ISS-3"]
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "worktree", payload: payload),
            .worktree(identifier: "ISS-3", path: "/w/ISS-3")
        )
    }

    func testSessionWithPid() {
        let payload: [String: Any] = ["identifier": "ISS-4", "session_id": "s-1", "pid": 4242]
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "session", payload: payload),
            .session(identifier: "ISS-4", sessionId: "s-1", pid: 4242)
        )
    }

    func testSessionWithoutPidIsNil() {
        let payload: [String: Any] = ["identifier": "ISS-4", "session_id": "s-1"]
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "session", payload: payload),
            .session(identifier: "ISS-4", sessionId: "s-1", pid: nil)
        )
    }

    func testLogDefaultsLevelToInfoAndAnsiFalse() {
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "log", payload: ["line": "hello"]),
            .log(line: "hello", level: "info", ansi: false)
        )
    }

    func testLogWithAnsiFlag() {
        let payload: [String: Any] = ["line": "\u{1B}[31mred", "level": "debug", "ansi": true]
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "log", payload: payload),
            .log(line: "\u{1B}[31mred", level: "debug", ansi: true)
        )
    }

    func testUnknownTypeCarriesRawType() {
        XCTAssertEqual(
            ScanPayloadParser.parse(type: "mystery", payload: ["whatever": 1]),
            .unknown(type: "mystery")
        )
    }
}
