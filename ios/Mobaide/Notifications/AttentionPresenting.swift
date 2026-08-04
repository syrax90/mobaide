import Foundation

/// Notification sink the `AttentionCoordinator` drives. Abstracted so the
/// coordinator's post/remove/reconcile logic is testable without a live
/// `UNUserNotificationCenter`. Live implementation: `NotificationService`.
protocol AttentionPresenting: Sendable {
    /// Post (or replace in place) the attention notification for `session`;
    /// a session whose attention maps to no content is a no-op.
    func post(_ session: SessionResponse) async
    /// Remove the delivered/pending notification for a session id.
    func remove(sessionId: String)
    /// Ids of currently delivered notifications (for foreground reconcile).
    func deliveredSessionIds() async -> Set<String>
    /// Request alert+sound authorization, reporting whether it was granted; a
    /// no-op returning the standing decision once the user has decided.
    /// The system prompt blocks on the user and cannot be shown at all while
    /// the app is backgrounded, so this may only be awaited from a detached,
    /// cancellable task — never from the coordinator's serial auth-state or
    /// event loops, where a pending dialog would hold a teardown or a clearing
    /// `session.updated` behind it.
    func requestAuthorization() async -> Bool
    /// Drop every delivered/pending notification (cross-account teardown).
    func removeAll()
}
