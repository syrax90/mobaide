import Foundation

/// Sums the spans in which a terminal was in healthy use, on a monotonic clock.
struct HealthyTimeMeter {
    private let now: () -> TimeInterval
    private var since: TimeInterval?
    private var total: TimeInterval = 0

    init(now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.now = now
    }

    mutating func update(healthy: Bool) {
        if healthy, since == nil {
            since = now()
        } else if !healthy, let start = since {
            total += now() - start
            since = nil
        }
    }

    /// Closes any open span and returns the total, starting the next visit from zero.
    mutating func finish() -> TimeInterval {
        update(healthy: false)
        defer { total = 0 }
        return total
    }
}
