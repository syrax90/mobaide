import Foundation
@testable import Mobaide

/// Buffers core events so tests can assert exact sequences with timeouts
/// instead of racing the actor's async event delivery.
final class EventCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var buffer: [WebSocketEvent] = []
    private var readIndex = 0
    private var task: Task<Void, Never>?

    func start(_ stream: AsyncStream<WebSocketEvent>) {
        task = Task { [weak self] in
            for await event in stream {
                self?.append(event)
            }
        }
    }

    var all: [WebSocketEvent] {
        lock.withLock { buffer }
    }

    func next(timeout: Duration = .seconds(5)) async throws -> WebSocketEvent {
        let deadline = ContinuousClock.now.advanced(by: timeout)
        while ContinuousClock.now < deadline {
            let event: WebSocketEvent? = lock.withLock {
                guard readIndex < buffer.count else { return nil }
                let next = buffer[readIndex]
                readIndex += 1
                return next
            }
            if let event {
                return event
            }
            try await Task.sleep(for: .milliseconds(1))
        }
        throw CollectorTimeout()
    }

    /// Lets in-flight tasks settle, then reports whether every buffered event
    /// has already been consumed via `next()`.
    func noPendingEvents() async -> Bool {
        try? await Task.sleep(for: .milliseconds(50))
        return lock.withLock { readIndex == buffer.count }
    }

    private func append(_ event: WebSocketEvent) {
        lock.withLock { buffer.append(event) }
    }
}

struct CollectorTimeout: Error {}
