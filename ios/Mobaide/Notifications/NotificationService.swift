import UserNotifications
import os

/// `UNUserNotificationCenter` wrapper + delegate (port of Android
/// `AttentionNotifier` plus the notification-facing half of
/// `SessionEventService`). Builds attention notifications keyed by
/// `session_id`, suppresses the currently-viewed session in the foreground,
/// and hands a tapped notification's session id to the deep-link router.
///
/// `@unchecked Sendable`: `UNUserNotificationCenterDelegate` requires
/// `NSObject`, which is not `Sendable`. Every stored property is either
/// immutable and `Sendable` (the tracker is thread-safe, the center is a
/// thread-safe singleton, `onTap` is `@Sendable`) or lock-guarded
/// (`authorizationRequest`), so there is no unsynchronised mutable state.
final class NotificationService: NSObject, UNUserNotificationCenterDelegate, AttentionPresenting, @unchecked Sendable {
    /// Loud delivery: heads-up banner + sound, on the Lock Screen and while
    /// foregrounded. Deliberately excludes `.provisional`, which grants
    /// quiet-only delivery (Notification Center, no banner, no sound) and
    /// additionally caps whatever `willPresent` returns down to `.list`.
    static let authorizationOptions: UNAuthorizationOptions = [.alert, .sound, .badge]

    private let tracker: ForegroundSessionTracker
    private let center: UNUserNotificationCenter
    private let onTap: @Sendable (String) -> Void

    /// Single-flight slot for an in-flight `requestAuthorization`, so the
    /// coordinator's fire-and-forget request and a racing inline request from
    /// `post` share one system prompt. Cleared when a request resolves without
    /// a grant: a request made while the app is not active shows no prompt, so
    /// it has to stay retryable.
    private let authorizationRequest = OSAllocatedUnfairLock<Task<Bool, Never>?>(initialState: nil)

    init(
        tracker: ForegroundSessionTracker,
        center: UNUserNotificationCenter = .current(),
        onTap: @escaping @Sendable (String) -> Void
    ) {
        self.tracker = tracker
        self.center = center
        self.onTap = onTap
    }

    /// Session id carried by a notification. Locally-posted alerts use the
    /// request `identifier`; a remote APNs alert carries it in the payload's
    /// custom `session_id` (its `apns-collapse-id` — which iOS surfaces as
    /// the identifier — also equals the session id, but the explicit field
    /// doesn't depend on that transport detail).
    static func sessionId(
        from userInfo: [AnyHashable: Any], identifier: String
    ) -> String {
        (userInfo["session_id"] as? String) ?? identifier
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

    func requestAuthorization() {
        _ = startAuthorizationRequest()
    }

    /// Starts the authorization request, or joins the one already in flight, so
    /// concurrent callers never queue a second system prompt.
    private func startAuthorizationRequest() -> Task<Bool, Never> {
        authorizationRequest.withLock { slot in
            if let inFlight = slot { return inFlight }
            let task = Task { [center] in
                (try? await center.requestAuthorization(options: Self.authorizationOptions)) ?? false
            }
            slot = task
            return task
        }
    }

    private func isAuthorized() async -> Bool {
        switch await center.notificationSettings().authorizationStatus {
        case .authorized:
            return true
        case .provisional:
            // Builds before this one asked for `.provisional`, which leaves the
            // install on quiet-only delivery. Re-requesting without it promotes
            // to full authorization (provisional records a provisional grant,
            // not a user decision, so the prompt still shows). Fire-and-forget:
            // provisional already delivers, so this alert must not wait on it.
            requestAuthorization()
            return true
        case .notDetermined:
            // The coordinator's auth task requests authorization on first
            // `.fullyConnected`, but it and the event loop are unordered, so the
            // first `.sessionAttention` can arrive here first. Join the same
            // single-flight request so that first alert is never dropped.
            let granted = await startAuthorizationRequest().value
            if !granted {
                authorizationRequest.withLock { $0 = nil }
            }
            return granted
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
            eventSessionId: Self.sessionId(
                from: notification.request.content.userInfo,
                identifier: notification.request.identifier
            )
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
        onTap(
            Self.sessionId(
                from: response.notification.request.content.userInfo,
                identifier: response.notification.request.identifier
            )
        )
    }
}
