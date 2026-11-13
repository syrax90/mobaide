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

    func testAnUntitledCursorSessionIsNamedAfterItsKind() {
        let untitled = makeSessionResponse(title: " ", projectName: nil, kind: .cursor)

        XCTAssertEqual(AttentionContent.label(for: untitled), "Cursor session")
        XCTAssertEqual(
            AttentionContent.label(for: makeSessionResponse(title: nil, projectName: "proj", kind: .cursor)),
            "proj",
            "the project still wins over the kind"
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
