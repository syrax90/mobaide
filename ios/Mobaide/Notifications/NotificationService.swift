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
final class NotificationService: NSObject, @MainActor UNUserNotificationCenterDelegate, AttentionPresenting, @unchecked Sendable {
    /// Loud delivery: heads-up banner + sound, on the Lock Screen and while
    /// foregrounded. Deliberately excludes `.provisional`, which grants
    /// quiet-only delivery (Notification Center, no banner, no sound) and
    /// additionally caps whatever `willPresent` returns down to `.list`.
    static let authorizationOptions: UNAuthorizationOptions = [.alert, .sound, .badge]

    private let tracker: ForegroundSessionTracker
    private let center: UNUserNotificationCenter
    private let onTap: @MainActor @Sendable (String, String?) -> Void

    /// Single-flight slot for an in-flight `requestAuthorization`, so the
    /// coordinator's fire-and-forget request and a racing inline request from
    /// `post` share one system prompt.
    private let authorizationRequest = OSAllocatedUnfairLock<Task<Bool, Never>?>(initialState: nil)

    /// Bumped by every `removeAll()`. A `post` suspended across a teardown
    /// compares the epoch it captured before suspending and drops the add,
    /// keeping notifications agent-scoped.
    private let teardownEpoch = OSAllocatedUnfairLock<UInt64>(initialState: 0)

    /// Announces a grant to whoever can recover the alerts dropped while
    /// authorization was undecided. It hangs off the request itself rather than
    /// off any one caller because `post` starts its own retries whenever it
    /// finds `.notDetermined` — an outcome nobody would otherwise be waiting on.
    private let onAuthorizationGranted = OSAllocatedUnfairLock<(@Sendable () -> Void)?>(initialState: nil)

    init(
        tracker: ForegroundSessionTracker,
        center: UNUserNotificationCenter = .current(),
        onTap: @escaping @MainActor @Sendable (String, String?) -> Void
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

    /// Agent that raised the alert, or nil when the notification carries no
    /// such claim. Only remote alerts do: the relay stamps `agent_id` from the
    /// authenticated tunnel, while a locally-posted alert has an empty
    /// `userInfo` and is by construction the connected agent's. Blank counts as
    /// no claim — the dispatcher sends `str(payload.get("agent_id", ""))`, so a
    /// payload without one arrives as `""` rather than absent.
    static func agentId(from userInfo: [AnyHashable: Any]) -> String? {
        (userInfo["agent_id"] as? String).flatMap { $0.isBlank ? nil : $0 }
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

    @discardableResult
    func post(_ session: SessionResponse) async -> Bool {
        guard let request = Self.makeRequest(for: session) else { return false }
        // `isAuthorized()` can suspend for as long as the user takes to answer
        // the permission prompt. An agent-scoped teardown (logout, agent
        // switch/takeover) landing in that window runs `removeAll()`, but does
        // not cancel this task — so re-check the epoch before adding, or the
        // previous agent's alert reappears after the teardown cleared it.
        let epoch = teardownEpoch.withLock { $0 }
        guard await isAuthorized() else { return false }
        guard teardownEpoch.withLock({ $0 }) == epoch else { return false }
        do {
            try await center.add(request)
            return true
        } catch {
            return false
        }
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

    func requestAuthorization() {
        _ = startAuthorizationRequest()
    }

    /// Wires the grant handler. Set by `AppContainer` once the coordinator
    /// exists, which is after this service (the coordinator takes it as its
    /// presenter) — the same late-binding shape as `attachPushDelegate`.
    func setOnAuthorizationGranted(_ handler: @escaping @Sendable () -> Void) {
        onAuthorizationGranted.withLock { $0 = handler }
    }

    /// Starts the authorization request, or joins the one already in flight, so
    /// concurrent callers never queue a second system prompt. The task evicts
    /// itself when it resolves: the slot tracks only an *in-flight* request, it
    /// is not a memo of the outcome. A request made while the app is not active
    /// resolves `false` without ever showing a prompt, and caching that would
    /// strand a `.provisional` install — which re-requests on every alert —
    /// quiet forever. Re-prompt suppression comes from the `authorizationStatus`
    /// switch below, not from this slot.
    ///
    /// Creating the task and publishing it into the slot both happen while the
    /// lock is held, and that ordering is load-bearing: a request that resolves
    /// immediately (the app is inactive, so no prompt is shown) would otherwise
    /// evict before it was ever published, and the assignment would then cache a
    /// finished task forever. Because eviction needs this same lock it blocks
    /// until publication has happened, so the slot always ends up `nil` rather
    /// than stale. Do not hoist the `Task` out of the critical section.
    private func startAuthorizationRequest() -> Task<Bool, Never> {
        // `OSAllocatedUnfairLock` is a struct over heap storage, so this copy is
        // the same lock and the same slot the task below clears.
        let slotLock = authorizationRequest
        return slotLock.withLock { slot in
            if let inFlight = slot { return inFlight }
            let grantHandler = onAuthorizationGranted
            let task = Task { [center] in
                let granted = (try? await center.requestAuthorization(options: Self.authorizationOptions)) ?? false
                slotLock.withLock { $0 = nil }
                if granted {
                    grantHandler.withLock { $0 }?()
                }
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

    // Both witnesses below MUST stay `@MainActor`. Swift bridges an `async`
    // delegate method to its ObjC completion-handler selector through an
    // `@isolated(any)` task that adopts the *callee's* isolation, so a
    // nonisolated witness calls UIKit's completion handler from the
    // cooperative pool. UIKit answers that handler by running
    // `_updateSnapshotAndStateRestorationWithAction:` — the app-switcher
    // snapshot and state-restoration write it performs when a response
    // launched or resumed the app — which asserts main-thread affinity and
    // aborts the process. It only fires when the app was not already active,
    // which is why an off-main handler looked harmless in the foreground.
    //
    // The isolated conformance carries that isolation: the SDK requirements are
    // nonisolated over non-Sendable parameters, which no plain `@MainActor`
    // witness could satisfy. Same shape as `QrScannerView`'s
    // `@MainActor AVCaptureMetadataOutputObjectsDelegate` and `TerminalHostView`'s
    // `@MainActor TerminalViewDelegate`.

    /// Called only while the app is in the foreground. Suppress iff the user
    /// is already viewing this session; otherwise present a banner (a
    /// different session's alert still fires while the app is open).
    @MainActor
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

    /// The ids are handed to the router, which stashes them until
    /// `.fullyConnected` on a cold start or navigates immediately when warm.
    /// Dispatched inline rather than from a detached task so the route is
    /// already pushed by the time UIKit takes its post-response snapshot.
    @MainActor
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let userInfo = response.notification.request.content.userInfo
        onTap(
            Self.sessionId(
                from: userInfo,
                identifier: response.notification.request.identifier
            ),
            Self.agentId(from: userInfo)
        )
    }
}
