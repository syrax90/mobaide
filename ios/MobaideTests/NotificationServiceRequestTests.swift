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
}
