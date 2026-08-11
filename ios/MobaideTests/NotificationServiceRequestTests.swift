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

    func testAgentIdReadFromRemotePayloadAndNilForLocalAlerts() {
        // The relay stamps `agent_id` from the authenticated tunnel
        // (`central_server/push/sender.py`); a locally posted alert carries no
        // userInfo and is the connected agent's by construction, and a nil id
        // is what keeps that path replaying without an agent switch.
        XCTAssertEqual(
            NotificationService.agentId(from: ["session_id": "sess-remote", "agent_id": "a1"]),
            "a1"
        )
        XCTAssertNil(NotificationService.agentId(from: [:]))
    }

    func testBlankAgentIdIsNotAnAgentClaim() {
        // The dispatcher stamps `str(payload.get("agent_id", ""))`, so a push
        // raised without an agent id arrives as `""` rather than absent — an
        // empty claim must not send the deep link switching to a nameless agent.
        XCTAssertNil(NotificationService.agentId(from: ["session_id": "sess-remote", "agent_id": ""]))
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
