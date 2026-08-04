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
        XCTAssertEqual(request?.content.sound, .default)
        XCTAssertNil(request?.trigger)
    }

    func testAuthorizationOptionsRequestLoudDeliveryNotProvisional() {
        let options = NotificationService.authorizationOptions
        XCTAssertTrue(options.contains(.alert))
        XCTAssertTrue(options.contains(.sound))
        XCTAssertTrue(options.contains(.badge))
        XCTAssertFalse(
            options.contains(.provisional),
            "provisional grants quiet-only delivery: no banner, no sound, and it caps willPresent to .list"
        )
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
