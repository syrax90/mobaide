#if DEBUG
import Foundation

/// Debug builds only: the once-per-process permission to sign in with the
/// credentials `tests/e2e/manual_stack.py` passes at launch (see
/// `LoginModel.signInWithDevLoginIfRequested`). A manual sign-out spends it,
/// so signing out stays signed out even when the app started signed in and
/// the login screen first appears only then.
enum DevLoginAllowance {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var spent = false

    /// True for the first caller only.
    static func take() -> Bool {
        lock.withLock {
            defer { spent = true }
            return !spent
        }
    }

    static func discard() {
        lock.withLock { spent = true }
    }
}
#endif
