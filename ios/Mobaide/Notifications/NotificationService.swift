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
/// (`authorizationRequest`, `teardownEpoch`), so there is no unsynchronised
/// mutable state.
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
    /// `post` share one system prompt.
    private let authorizationRequest = OSAllocatedUnfairLock<Task<Bool, Never>?>(initialState: nil)

    /// Bumped by every `removeAll()`. A `post` suspended across a teardown
    /// compares the epoch it captured before suspending and drops the add,
    /// keeping notifications agent-scoped.
    private let teardownEpoch = OSAllocatedUnfairLock<UInt64>(initialState: 0)

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
        // `isAuthorized()` can suspend for as long as the user takes to answer
        // the permission prompt. An agent-scoped teardown (logout, agent
        // switch/takeover) landing in that window runs `removeAll()`, but does
        // not cancel this task — so re-check the epoch before adding, or the
        // previous agent's alert reappears after the teardown cleared it.
        let epoch = teardownEpoch.withLock { $0 }
        guard await isAuthorized() else { return }
        guard teardownEpoch.withLock({ $0 }) == epoch else { return }
        try? await center.add(request)
    }

    func remove(sessionId: String) {
        center.removeDeliveredNotifications(withIdentifiers: [sessionId])
        center.removePendingNotificationRequests(withIdentifiers: [sessionId])
    }

    func removeAll() {
        teardownEpoch.withLock { $0 &+= 1 }
        center.removeAllDeliveredNotifications()
        center.removeAllPendingNotificationRequests()
    }

    func deliveredSessionIds() async -> Set<String> {
        Set(await center.deliveredNotifications().map { $0.request.identifier })
    }

    func requestAuthorization() async -> Bool {
        await startAuthorizationRequest().value
    }

    /// Starts the authorization request, or joins the one already in flight, so
    /// concurrent callers never queue a second system prompt. The task evicts
    /// itself when it resolves: the slot tracks only an *in-flight* request, it
    /// is not a memo of the outcome. A request made while the app is not active
    /// resolves `false` without ever showing a prompt, and caching that would
    /// strand a `.provisional` install — which re-requests on every alert —
    /// quiet forever. Re-prompt suppression comes from the `authorizationStatus`
    /// switch below, not from this slot.
    private func startAuthorizationRequest() -> Task<Bool, Never> {
        let slotLock = authorizationRequest
        return slotLock.withLock { slot in
            if let inFlight = slot { return inFlight }
            let task = Task { [center] in
                let granted = (try? await center.requestAuthorization(options: Self.authorizationOptions)) ?? false
                slotLock.withLock { $0 = nil }
                return granted
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
            _ = startAuthorizationRequest()
            return true
        case .notDetermined:
            // Never await the prompt here. `post` runs on the coordinator's
            // serial event loop, so a pending dialog holds every later event
            // behind it — including a `session.updated` that clears this very
            // attention, which would then be applied only *after* this alert
            // was added, flashing a loud notification for resolved attention.
            // Start the request and drop this alert; on a grant the coordinator
            // reconciles from a fresh snapshot, which re-posts whatever still
            // needs attention and naturally omits whatever no longer does.
            _ = startAuthorizationRequest()
            return false
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
