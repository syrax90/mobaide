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

    func testAnswerableChatPermissionCarriesTheActionCategoryAndItsRequest() {
        let session = makeSessionResponse(
            id: "sess-42",
            attention: "awaiting_permission",
            title: "Bug fix",
            attentionRequest: Self.chatRequest(choices: ["allow_once", "reject_once"])
        )
        let request = NotificationService.makeRequest(for: session)
        XCTAssertEqual(request?.content.categoryIdentifier, NotificationService.permissionCategory)
        XCTAssertEqual(request?.content.userInfo["request_id"] as? String, "r:3:a1b2c3")
        // Only the local alert shows the request's title: an APNs alert never carries it.
        XCTAssertEqual(request?.content.body, "Bug fix wants to run a command\nnpm test")
    }

    func testRequestThisBuildCannotAnswerGetsAPlainAlert() {
        // A newer agent that offers other choices: no action may send one it
        // did not offer.
        let session = makeSessionResponse(
            attention: "awaiting_permission",
            attentionRequest: Self.chatRequest(choices: ["allow_once", "allow_always"])
        )
        let request = NotificationService.makeRequest(for: session)
        XCTAssertEqual(request?.content.categoryIdentifier, "")
        XCTAssertNil(request?.content.userInfo["request_id"])
    }

    func testAllowAsksForAnUnlockAndDenyDoesNot() {
        let category = NotificationService.permissionCategories()
            .first { $0.identifier == NotificationService.permissionCategory }
        let options = Dictionary(
            uniqueKeysWithValues: (category?.actions ?? []).map { ($0.identifier, $0.options) }
        )
        XCTAssertEqual(Set(options.keys), [NotificationService.allowActionId, NotificationService.denyActionId])
        XCTAssertEqual(options[NotificationService.allowActionId], [.authenticationRequired])
        // Deny stays usable on the Lock Screen and on a paired Apple Watch.
        XCTAssertEqual(options[NotificationService.denyActionId], [])
    }

    func testPermissionActionReadsTheTappedChoiceAndTheAlertsRequest() {
        XCTAssertEqual(
            NotificationService.permissionAction(
                actionIdentifier: NotificationService.allowActionId,
                userInfo: ["request_id": "r:3:a1b2c3"],
                identifier: "sess-42"
            ),
            PermissionAction(sessionId: "sess-42", agentId: nil, requestId: "r:3:a1b2c3", choice: .allow)
        )
        XCTAssertEqual(
            NotificationService.permissionAction(
                actionIdentifier: NotificationService.denyActionId,
                userInfo: ["session_id": "sess-remote", "agent_id": "a1", "request_id": "r:3:a1b2c3"],
                identifier: "apns-collapse-id"
            ),
            PermissionAction(sessionId: "sess-remote", agentId: "a1", requestId: "r:3:a1b2c3", choice: .deny)
        )
        // A plain tap opens the session; an action on an alert without a request answers nothing.
        XCTAssertNil(
            NotificationService.permissionAction(
                actionIdentifier: UNNotificationDefaultActionIdentifier,
                userInfo: ["request_id": "r:3:a1b2c3"],
                identifier: "sess-42"
            )
        )
        XCTAssertNil(
            NotificationService.permissionAction(
                actionIdentifier: NotificationService.allowActionId,
                userInfo: [:],
                identifier: "sess-42"
            )
        )
    }

    private static func chatRequest(choices: [String]) -> AttentionRequest {
        AttentionRequest(id: "r:3:a1b2c3", title: "npm test", tool_kind: "execute", choices: choices)
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
}
