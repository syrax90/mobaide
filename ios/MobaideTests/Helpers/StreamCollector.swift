import Foundation

/// Generic timeout-based drain for any `AsyncStream` (generalizes
/// `EventCollector` for AuthState/AgentEvent/BusConnectionState streams).
final class StreamCollector<Element: Sendable>: @unchecked Sendable {
    struct Timeout: Error {}

    private let lock = NSLock()
    private var buffer: [Element] = []
    private var task: Task<Void, Never>?

    init(_ stream: AsyncStream<Element>) {
        task = Task { [weak self] in
            for await element in stream {
                self?.append(element)
            }
        }
    }

    deinit {
        task?.cancel()
    }

    func next(timeout: Duration = .seconds(5)) async throws -> Element {
        let deadline = ContinuousClock.now.advanced(by: timeout)
        while ContinuousClock.now < deadline {
            if let element = popFirst() {
                return element
            }
            try await Task.sleep(for: .milliseconds(1))
        }
        throw Timeout()
    }

    /// Waits until `predicate` matches a drained element, discarding
    /// non-matching ones. Useful when interleaved state emissions are legal.
    func next(
        timeout: Duration = .seconds(5),
        where predicate: @escaping (Element) -> Bool
    ) async throws -> Element {
        let deadline = ContinuousClock.now.advanced(by: timeout)
        while ContinuousClock.now < deadline {
            if let element = popFirst() {
                if predicate(element) {
                    return element
                }
                continue
            }
            try await Task.sleep(for: .milliseconds(1))
        }
        throw Timeout()
    }

    func expectNoElement(within duration: Duration = .milliseconds(100)) async -> Bool {
        try? await Task.sleep(for: duration)
        return lock.withLock { buffer.isEmpty }
    }

    private func append(_ element: Element) {
        lock.withLock { buffer.append(element) }
    }

    private func popFirst() -> Element? {
        lock.withLock {
            buffer.isEmpty ? nil : buffer.removeFirst()
        }
    }
}
