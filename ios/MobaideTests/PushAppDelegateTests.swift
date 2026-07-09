import XCTest
@testable import Mobaide

@MainActor
final class PushAppDelegateTests: XCTestCase {
    func testTokenIsHexEncodedAndDeliveredToSubscriber() {
        let delegate = PushAppDelegate()
        var received: [String] = []
        delegate.onToken = { received.append($0) }

        delegate.application(
            UIApplication.shared,
            didRegisterForRemoteNotificationsWithDeviceToken: Data([0x00, 0x0A, 0xFF, 0x42])
        )

        XCTAssertEqual(received, ["000aff42"])
    }

    func testTokenArrivingBeforeSubscriptionIsReplayed() {
        let delegate = PushAppDelegate()
        delegate.application(
            UIApplication.shared,
            didRegisterForRemoteNotificationsWithDeviceToken: Data([0x01, 0x02])
        )

        var received: [String] = []
        delegate.onToken = { received.append($0) }

        XCTAssertEqual(received, ["0102"])
    }

    func testOnlyLatestBufferedTokenIsReplayed() {
        let delegate = PushAppDelegate()
        delegate.application(
            UIApplication.shared,
            didRegisterForRemoteNotificationsWithDeviceToken: Data([0x01])
        )
        delegate.application(
            UIApplication.shared,
            didRegisterForRemoteNotificationsWithDeviceToken: Data([0x02])
        )

        var received: [String] = []
        delegate.onToken = { received.append($0) }

        XCTAssertEqual(received, ["02"])
    }

    func testRotatedTokenIsDeliveredToExistingSubscriber() {
        let delegate = PushAppDelegate()
        var received: [String] = []
        delegate.onToken = { received.append($0) }

        delegate.application(
            UIApplication.shared,
            didRegisterForRemoteNotificationsWithDeviceToken: Data([0x01])
        )
        delegate.application(
            UIApplication.shared,
            didRegisterForRemoteNotificationsWithDeviceToken: Data([0x02])
        )

        XCTAssertEqual(received, ["01", "02"])
    }
}
