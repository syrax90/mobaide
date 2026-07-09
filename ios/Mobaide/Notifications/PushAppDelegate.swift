import UIKit

/// Minimal `UIApplicationDelegate` whose only job is receiving the APNs
/// device-token callbacks (SwiftUI apps have no other way to get them).
/// The token is hex-encoded and replayed to `onToken`: registration is
/// triggered by `PushRegistrationCoordinator` after auth, but the callback
/// can land before the coordinator wires itself up on a cold start, so the
/// latest token is buffered and delivered on subscription.
@MainActor
final class PushAppDelegate: NSObject, UIApplicationDelegate {
    private(set) var latestTokenHex: String?

    var onToken: ((String) -> Void)? {
        didSet {
            if let latestTokenHex { onToken?(latestTokenHex) }
        }
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        latestTokenHex = hex
        onToken?(hex)
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        // Simulators and dev machines without the push capability land here;
        // push registration is best-effort, everything else keeps working.
    }
}
