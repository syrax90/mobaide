import Foundation

/// Foreground-suppression rule (port of Android
/// `SessionEventService.shouldNotify`, negated). A notification is suppressed
/// only when the app is active AND the user is already viewing the very
/// session the event is for; an alert for a different session fires even
/// while the app is open.
enum AttentionSuppression {
    static func shouldSuppress(appActive: Bool, visibleSessionId: String?, eventSessionId: String) -> Bool {
        appActive && visibleSessionId == eventSessionId
    }
}
