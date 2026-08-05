import Foundation

/// Notification sink the `AttentionCoordinator` drives. Abstracted so the
/// coordinator's post/remove/reconcile logic is testable without a live
/// `UNUserNotificationCenter`. Live implementation: `NotificationService`.
protocol AttentionPresenting: Sendable {
    /// Post (or replace in place) the attention notification for `session`;
    /// a session whose attention maps to no content is a no-op. Returns whether
    /// the alert actually reached Notification Center — a drop (unmapped
    /// content, authorization still undecided, a teardown mid-flight) must not
    /// be remembered as surfaced, or the grant-driven recovery reconcile would
    /// skip the very alerts it exists to replay.
    @discardableResult
    func post(_ session: SessionResponse) async -> Bool
    /// Remove the delivered/pending notification for a session id.
    func remove(sessionId: String)
    /// Ids of currently delivered notifications (for foreground reconcile).
    func deliveredSessionIds() async -> Set<String>
    /// Request alert+sound authorization; a no-op once the user has decided.
    /// Synchronous and fire-and-forget by contract: the system prompt blocks on
    /// the user and cannot be shown at all while the app is backgrounded, so
    /// awaiting it on either of the coordinator's serial loops would hold a
    /// teardown, or a clearing `session.updated`, behind the dialog. A grant is
    /// reported back out of band, not by this call.
    func requestAuthorization()
    /// Drop every delivered/pending notification (cross-account teardown).
    func removeAll()
}
