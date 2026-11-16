import XCTest
@testable import Mobaide

final class AttentionContentTests: XCTestCase {
    func testLabelPrefersTitleThenWorkspaceThenFallback() {
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: "My Title", workspaceName: "proj")), "My Title")
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: nil, workspaceName: "proj")), "proj")
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: "   ", workspaceName: "proj")), "proj")
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: nil, workspaceName: nil)), "Claude session")
        XCTAssertEqual(AttentionContent.label(for: makeSessionResponse(title: "  ", workspaceName: "  ")), "Claude session")
    }

    func testAnUntitledCursorSessionIsNamedAfterItsKind() {
        let untitled = makeSessionResponse(title: " ", workspaceName: nil, kind: .cursor)

        XCTAssertEqual(AttentionContent.label(for: untitled), "Cursor session")
        XCTAssertEqual(
            AttentionContent.label(for: makeSessionResponse(title: nil, workspaceName: "proj", kind: .cursor)),
            "proj",
            "the workspace still wins over the kind"
        )
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
