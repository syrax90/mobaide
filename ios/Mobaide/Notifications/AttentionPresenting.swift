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
    /// Request provisional (quiet) authorization; a silent no-op once decided.
    func requestProvisionalAuthorization() async
    /// Drop every delivered/pending notification (cross-account teardown).
    func removeAll()
}
