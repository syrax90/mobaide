import XCTest
@testable import Mobaide

final class AttentionContentTests: XCTestCase {
    func testLabelPrefersTitleThenProjectThenFallback() {
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: "My Title", projectName: "proj")), "My Title")
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: nil, projectName: "proj")), "proj")
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: "   ", projectName: "proj")), "proj")
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: nil, projectName: nil)), "Claude session")
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: "  ", projectName: "  ")), "Claude session")
    }

    func testAwaitingPermission() {
        let content = AttentionContent.content(for: makeSessionResponse(attention: "awaiting_permission", title: "Bug fix"))
        XCTAssertEqual(content?.title, "Permission needed")
        XCTAssertEqual(content?.body, "Bug fix is waiting for your approval")
    }

    func testAwaitingOsPermission() {
        let content = AttentionContent.content(for: makeSessionResponse(attention: "awaiting_os_permission", title: "Bug fix"))
        XCTAssertEqual(content?.title, "Action needed on your Mac")
        XCTAssertEqual(
            content?.body,
            "Bug fix is paused — your Mac is asking to allow file access. Approve it on the Mac to continue."
        )
    }

    func testTurnFinished() {
        let content = AttentionContent.content(for: makeSessionResponse(attention: "turn_finished", title: "Bug fix"))
        XCTAssertEqual(content?.title, "Your move")
        XCTAssertEqual(content?.body, "Bug fix finished its turn — tap to continue")
    }

    func testTurnFailed() {
        let content = AttentionContent.content(for: makeSessionResponse(attention: "turn_failed", title: "Bug fix"))
        XCTAssertEqual(content?.title, "Turn failed")
        XCTAssertEqual(content?.body, "Bug fix hit an error — tap to see what happened")
    }

    func testUntitledSessionIsNamedAfterItsKind() {
        let cases: [(SessionKind, String?, String)] = [
            (.claude, nil, "Claude session"),
            (.codex, nil, "Codex session"),
            (.antigravity, nil, "Antigravity session"),
            (.autoScan, nil, "Auto Scan session"),
            (.shell, nil, "Terminal session"),
            (.custom("custom_0123456789ab"), "My Linter", "My Linter session"),
            (.custom("custom_0123456789ab"), "  ", "Agent session"),
            (.custom("custom_0123456789ab"), nil, "Agent session"),
        ]
        for (kind, kindLabel, expected) in cases {
            let session = makeSessionResponse(
                attention: "turn_finished", title: " ", projectName: "", kind: kind, kindLabel: kindLabel
            )
            XCTAssertEqual(
                AttentionContent.content(for: session)?.body,
                "\(expected) finished its turn — tap to continue",
                "kind \(kind.wireName)"
            )
        }
    }

    func testTitleAndProjectStillWinOverTheKind() {
        XCTAssertEqual(
            AttentionContent.label(for: makeSessionResponse(title: "My Title", projectName: "proj", kind: .codex)),
            "My Title"
        )
        XCTAssertEqual(
            AttentionContent.label(for: makeSessionResponse(title: nil, projectName: "proj", kind: .codex)),
            "proj"
        )
    }

    func testEndedWithReason() {
        let content = AttentionContent.content(for: makeSessionResponse(attention: "ended", title: "Bug fix", exitReason: "completed"))
        XCTAssertEqual(content?.title, "Session ended (completed)")
        XCTAssertEqual(content?.body, "Bug fix")
    }

    func testEndedWithoutReason() {
        let content = AttentionContent.content(for: makeSessionResponse(attention: "ended", title: "Bug fix", exitReason: nil))
        XCTAssertEqual(content?.title, "Session ended")
        XCTAssertEqual(content?.body, "Bug fix")
    }

    func testEndedWithBlankReasonHasNoSuffix() {
        let content = AttentionContent.content(for: makeSessionResponse(attention: "ended", title: "Bug fix", exitReason: "   "))
        XCTAssertEqual(content?.title, "Session ended")
    }

    func testEndedFallsBackToClaudeSessionLabel() {
        let content = AttentionContent.content(for: makeSessionResponse(attention: "ended", title: nil, projectName: nil))
        XCTAssertEqual(content?.body, "Claude session")
    }

    func testUnknownAndNilAttentionProduceNoContent() {
        XCTAssertNil(AttentionContent.content(for: makeSessionResponse(attention: nil)))
        XCTAssertNil(AttentionContent.content(for: makeSessionResponse(attention: "some_future_reason")))
    }

    func testAttentionSessionsFiltersToNotifiableInSnapshotOrder() {
        let sessions = [
            makeSessionResponse(id: "a", attention: "awaiting_permission"),
            makeSessionResponse(id: "b", attention: nil),
            makeSessionResponse(id: "c", attention: "ended"),
            makeSessionResponse(id: "d", attention: "unknown_reason"),
        ]
        XCTAssertEqual(
            AttentionContent.attentionSessions(in: sessions).map(\.session_id),
            ["a", "c"],
            "reconcile posts and adopts its baseline in snapshot order"
        )
    }
}
