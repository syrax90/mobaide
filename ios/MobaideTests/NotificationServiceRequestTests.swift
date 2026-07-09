import UserNotifications
import XCTest
@testable import Mobaide

final class NotificationServiceRequestTests: XCTestCase {
    func testRequestUsesSessionIdForIdentityAndThread() {
        let session = makeSessionResponse(id: "sess-42", attention: "awaiting_permission", title: "Bug fix")
        let request = NotificationService.makeRequest(for: session)
        XCTAssertEqual(request?.identifier, "sess-42")
        XCTAssertEqual(request?.content.threadIdentifier, "sess-42")
        XCTAssertEqual(request?.content.title, "Permission needed")
        XCTAssertEqual(request?.content.body, "Bug fix is waiting for your approval")
        XCTAssertNotNil(request?.content.sound)
        XCTAssertNil(request?.trigger)
    }

    func testRequestNilForUnnotifiableAttention() {
        XCTAssertNil(NotificationService.makeRequest(for: makeSessionResponse(attention: nil)))
        XCTAssertNil(NotificationService.makeRequest(for: makeSessionResponse(attention: "future_reason")))
    }

    func testSessionIdPrefersUserInfoOverIdentifier() {
        // Remote APNs alerts carry the session id in the payload's custom
        // data; the request identifier is transport-controlled.
        let sessionId = NotificationService.sessionId(
            from: ["session_id": "sess-remote", "agent_id": "a1"],
            identifier: "apns-collapse-id"
        )
        XCTAssertEqual(sessionId, "sess-remote")
    }

    func testSessionIdFallsBackToIdentifierForLocalAlerts() {
        XCTAssertEqual(
            NotificationService.sessionId(from: [:], identifier: "sess-local"),
            "sess-local"
        )
        // A non-string session_id (malformed payload) also falls back.
        XCTAssertEqual(
            NotificationService.sessionId(
                from: ["session_id": 42], identifier: "sess-local"
            ),
            "sess-local"
        )
    }
}
