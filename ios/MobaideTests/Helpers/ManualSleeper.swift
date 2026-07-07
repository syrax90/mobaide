import Foundation
@testable import Mobaide

/// A `Sleeper` whose `sleep(for:)` blocks until the test calls `release()`, so
/// deferred-commit / undo timing is deterministic (the real `RecordingSleeper`
/// returns immediately, which would fire a delayed commit instantly). Honors
/// cancellation: a `sleep` whose Task is cancelled throws `CancellationError`,
/// which is how "undo cancels the pending delete" is exercised.
final class ManualSleeper: Sleeper, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [Duration] = []
    private var waiters: [UUID: CheckedContinuation<Void, Error>] = [:]
    private var released = false

    var durations: [Duration] { lock.withLock { recorded } }

    func sleep(for duration: Duration) async throws {
        lock.withLock { recorded.append(duration) }
        let id = UUID()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
                let resumeNow: Bool = lock.withLock {
                    if released { return true }
                    waiters[id] = cont
                    return false
                }
                if resumeNow {
                    cont.resume()
                } else if Task.isCancelled {
                    // Cancelled between recording and registration: resolve now.
                    let pending: CheckedContinuation<Void, Error>? = lock.withLock { waiters.removeValue(forKey: id) }
                    pending?.resume(throwing: CancellationError())
                }
            }
        } onCancel: {
            let pending: CheckedContinuation<Void, Error>? = lock.withLock { waiters.removeValue(forKey: id) }
            pending?.resume(throwing: CancellationError())
        }
    }

    /// Let every currently-blocked (and every future) sleep return normally —
    /// i.e. the 5-second window elapses.
    func release() {
        let pending: [CheckedContinuation<Void, Error>] = lock.withLock {
            released = true
            let values = Array(waiters.values)
            waiters.removeAll()
            return values
        }
        pending.forEach { $0.resume() }
    }
}
