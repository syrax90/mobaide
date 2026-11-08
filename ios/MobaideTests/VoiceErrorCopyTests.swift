import XCTest
@testable import Mobaide

/// The cleanup failure copy names the CLI of the session kind the cleanup ran for.
final class VoiceErrorCopyTests: XCTestCase {
    private func cleanup(_ code: String, kind: String?) -> String {
        VoiceErrorCopy.cleanup(code, agent: "MacBook", kind: kind)
    }

    func testCleanupCopyNamesEachKindsCli() {
        let expected: [(String, String, String)] = [
            ("claude", "Claude Code", "claude update"),
            ("codex", "Codex", "codex update"),
            ("antigravity", "Antigravity", "agy update"),
        ]
        for (kind, name, command) in expected {
            XCTAssertEqual(cleanup(VoiceCodes.cleanupUnavailable, kind: kind), "\(name) isn't installed on MacBook.")
            XCTAssertEqual(
                cleanup(VoiceCodes.cleanupOutdated, kind: kind),
                "\(name) on MacBook is too old. Run \"\(command)\" there."
            )
            XCTAssertEqual(cleanup(VoiceCodes.cleanupTimeout, kind: kind), "\(name) took too long.")
            XCTAssertEqual(cleanup(VoiceCodes.cleanupFailed, kind: kind), "\(name) reported an error.")
            XCTAssertEqual(cleanup("something_new", kind: kind), "\(name) reported an error.")
        }
    }

    func testCleanupCopyForAnUnknownKindNamesTheSessionsAgentWithoutACommand() {
        for kind in ["shell", "custom_ab12", nil] {
            XCTAssertEqual(
                cleanup(VoiceCodes.cleanupUnavailable, kind: kind),
                "The session's AI agent isn't installed on MacBook."
            )
            XCTAssertEqual(
                cleanup(VoiceCodes.cleanupOutdated, kind: kind),
                "The session's AI agent on MacBook is too old. Update it there."
            )
            XCTAssertEqual(cleanup(VoiceCodes.cleanupTimeout, kind: kind), "The session's AI agent took too long.")
            XCTAssertEqual(cleanup(VoiceCodes.cleanupFailed, kind: kind), "The session's AI agent reported an error.")
        }
    }

    func testBusyAndNetworkCleanupCopyNamesNoCli() {
        for kind in ["claude", "codex", nil] {
            XCTAssertEqual(cleanup(VoiceCodes.cleanupBusy, kind: kind), "Another cleanup was still running.")
            XCTAssertEqual(cleanup(VoiceCodes.network, kind: kind), "The connection dropped.")
        }
    }
}
