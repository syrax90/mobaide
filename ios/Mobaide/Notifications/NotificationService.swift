import UserNotifications

/// `UNUserNotificationCenter` wrapper + delegate (port of Android
/// `AttentionNotifier` plus the notification-facing half of
/// `SessionEventService`). Builds attention notifications keyed by
/// `session_id`, suppresses the currently-viewed session in the foreground,
/// and hands a tapped notification's session id to the deep-link router.
///
/// `@unchecked Sendable`: `UNUserNotificationCenterDelegate` requires
/// `NSObject`, which is not `Sendable`. Every stored property is immutable
/// and `Sendable` (the tracker is thread-safe, the center is a thread-safe
/// singleton, `onTap` is `@Sendable`), so there is no mutable state to race.
final class NotificationService: NSObject, UNUserNotificationCenterDelegate, AttentionPresenting, @unchecked Sendable {
    private let tracker: ForegroundSessionTracker
    private let center: UNUserNotificationCenter
    private let onTap: @Sendable (String) -> Void

    init(
        tracker: ForegroundSessionTracker,
        center: UNUserNotificationCenter = .current(),
        onTap: @escaping @Sendable (String) -> Void
    ) {
        self.tracker = tracker
        self.center = center
        self.onTap = onTap
    }

    /// Notification request for a session's attention, or nil when the reason
    /// maps to no content. `identifier` and `threadIdentifier` are both the
    /// `session_id`, so a re-post replaces the previous alert in place (parity
    /// with Android's collapse-by-id) and a session's alerts group together.
    static func makeRequest(for session: SessionResponse) -> UNNotificationRequest? {
        guard let content = AttentionContent.content(for: session) else { return nil }
        let notification = UNMutableNotificationContent()
        notification.title = content.title
        notification.body = content.body
        notification.sound = .default
        notification.threadIdentifier = session.session_id
        return UNNotificationRequest(identifier: session.session_id, content: notification, trigger: nil)
    }

    // MARK: - AttentionPresenting

    func post(_ session: SessionResponse) async {
        guard let request = Self.makeRequest(for: session) else { return }
        guard await isAuthorized() else { return }
        try? await center.add(request)
    }

    func remove(sessionId: String) {
        center.removeDeliveredNotifications(withIdentifiers: [sessionId])
        center.removePendingNotificationRequests(withIdentifiers: [sessionId])
    }

    func removeAll() {
        center.removeAllDeliveredNotifications()
        center.removeAllPendingNotificationRequests()
    }

    func deliveredSessionIds() async -> Set<String> {
        Set(await center.deliveredNotifications().map { $0.request.identifier })
    }

    func requestProvisionalAuthorization() async {
        _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge, .provisional])
    }

    private func isAuthorized() async -> Bool {
        switch await center.notificationSettings().authorizationStatus {
        case .authorized, .provisional:
            return true
        case .notDetermined:
            // The coordinator's auth task requests provisional authorization on
            // first `.fullyConnected`, but it and the event loop are unordered,
            // so the first `.sessionAttention` can arrive here first. Request it
            // inline (silent, near-instant) so that first alert is never dropped.
            return (try? await center.requestAuthorization(options: [.alert, .sound, .badge, .provisional])) ?? false
        default:
            return false
        }
    }

    // MARK: - UNUserNotificationCenterDelegate

    /// Called only while the app is in the foreground. Suppress iff the user
    /// is already viewing this session; otherwise present a banner (a
    /// different session's alert still fires while the app is open).
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        let suppress = AttentionSuppression.shouldSuppress(
            appActive: true,
            visibleSessionId: tracker.visibleSessionId,
            eventSessionId: notification.request.identifier
        )
        return suppress ? [] : [.banner, .sound, .list]
    }

    /// A tapped notification deep-links to its session. The id is handed to
    /// the router, which stashes it until `.fullyConnected` on a cold start
    /// or navigates immediately when warm.
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        onTap(response.notification.request.identifier)
    }
}
