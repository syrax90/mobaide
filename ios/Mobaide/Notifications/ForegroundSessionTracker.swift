import Foundation
import os

/// In-memory holder of the session currently visible on screen. Set by the
/// terminal screen (iOS-04), read by notification delivery (iOS-09) so an
/// attention notification is suppressed only for the session being viewed.
final class ForegroundSessionTracker: Sendable {
    private let state = OSAllocatedUnfairLock<String?>(initialState: nil)

    var visibleSessionId: String? {
        state.withLock { $0 }
    }

    func setVisible(_ sessionId: String) {
        state.withLock { $0 = sessionId }
    }

    /// Clears only while the given session is still the visible one
    /// (compare-and-set), so a stale clear from a dismissed screen cannot
    /// wipe a newer screen's registration.
    func clearVisible(_ sessionId: String) {
        state.withLock { visible in
            if visible == sessionId {
                visible = nil
            }
        }
    }
}
