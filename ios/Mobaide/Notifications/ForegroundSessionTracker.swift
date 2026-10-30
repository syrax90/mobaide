import Foundation
import os

/// In-memory holder of the session currently visible on screen, and of the
/// sessions a child screen is keeping watched. Set by the terminal screen
/// (iOS-04), read by notification delivery (iOS-09) so an attention
/// notification is suppressed only for the session being viewed.
///
/// The watch holds are the "covered by a child screen" signal for the
/// terminal's unwatched-suspend governor. A session-scoped child (the git
/// Changes and Diff screens) hides the terminal when it is pushed, which alone
/// reads as "nobody is watching" and would eventually close the PTY socket —
/// an expensive mistake, since every reopen replays the whole ring. The child
/// therefore holds a ref-counted watch for exactly as long as it is on screen,
/// so every exit path (pop, a deep link pushing another terminal on top)
/// releases it without knowing why. Visibility is deliberately not
/// transferred: the terminal is *not* on screen, so its own attention alerts
/// should still fire.
final class ForegroundSessionTracker: Sendable {
    private struct State {
        var visible: String?
        var holds: [String: Int] = [:]
    }

    private let state = OSAllocatedUnfairLock(initialState: State())

    var visibleSessionId: String? {
        state.withLock { $0.visible }
    }

    func setVisible(_ sessionId: String) {
        state.withLock { $0.visible = sessionId }
    }

    /// Clears only while the given session is still the visible one
    /// (compare-and-set), so a stale clear from a dismissed screen cannot
    /// wipe a newer screen's registration.
    func clearVisible(_ sessionId: String) {
        state.withLock { state in
            if state.visible == sessionId {
                state.visible = nil
            }
        }
    }

    func holdWatch(_ sessionId: String) {
        state.withLock { $0.holds[sessionId, default: 0] += 1 }
    }

    func releaseWatch(_ sessionId: String) {
        state.withLock { state in
            let remaining = (state.holds[sessionId] ?? 0) - 1
            if remaining <= 0 {
                state.holds.removeValue(forKey: sessionId)
            } else {
                state.holds[sessionId] = remaining
            }
        }
    }

    func isHeld(_ sessionId: String) -> Bool {
        state.withLock { ($0.holds[sessionId] ?? 0) > 0 }
    }

    /// Some session-scoped child screen is on screen.
    var hasWatchHolds: Bool {
        state.withLock { !$0.holds.isEmpty }
    }
}
