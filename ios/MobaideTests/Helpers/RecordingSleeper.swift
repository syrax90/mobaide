import Foundation
@testable import Mobaide

/// Records requested durations and returns immediately, so backoff tests
/// observe the exact delay sequence without sleeping real time.
final class RecordingSleeper: Sleeper, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [Duration] = []

    var durations: [Duration] {
        lock.withLock { recorded }
    }

    func sleep(for duration: Duration) async throws {
        lock.withLock { recorded.append(duration) }
        try Task.checkCancellation()
    }
}
