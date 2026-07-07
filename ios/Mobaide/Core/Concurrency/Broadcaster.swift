import Foundation
import os

/// Multi-subscriber fan-out over per-subscriber `AsyncStream`s, replacing
/// Kotlin `StateFlow`/`SharedFlow`. A single shared `AsyncStream` is
/// single-consumer — sharing one iterator silently drops events — so every
/// subscriber gets its own stream/continuation pair.
///
/// Concurrency invariants:
/// - `emit(_:)` is synchronous and runs entirely under one lock acquisition,
///   so an actor can mutate its state and publish atomically with no
///   suspension (and therefore no reentrancy window) in between. Subscribers
///   observe emissions in exactly the order they were applied.
/// - `subscribe(replayLatest:)` registers the continuation and seeds the
///   replayed value under the same single lock acquisition — no gap where a
///   concurrent emit could be missed or double-delivered.
/// - `clearReplay()` (Kotlin `resetReplayCache` parity) only affects future
///   subscribers; values already yielded into a subscriber's buffered stream
///   are still delivered.
final class Broadcaster<Element: Sendable>: Sendable {
    /// Replay behavior for late subscribers.
    ///
    /// - `none`: SharedFlow(replay = 0) parity — late subscribers miss
    ///   earlier events.
    /// - `latest`: StateFlow parity — subscribers are seeded with the most
    ///   recent value.
    /// - `latestClearable`: SharedFlow(replay = 1) + `resetReplayCache`
    ///   parity — like `latest`, but the cached value can be cleared.
    enum Replay: Sendable {
        case none
        case latest
        case latestClearable
    }

    private struct State {
        var continuations: [UUID: AsyncStream<Element>.Continuation] = [:]
        var latest: Element?
    }

    private let replay: Replay
    private let conflate: (@Sendable (Element, Element) -> Bool)?
    private let state: OSAllocatedUnfairLock<State>

    init(replay: Replay = .none, conflatingWhen conflate: (@Sendable (Element, Element) -> Bool)? = nil) {
        self.replay = replay
        self.conflate = conflate
        self.state = OSAllocatedUnfairLock(initialState: State())
    }

    func emit(_ element: Element) {
        state.withLock { s in
            if let conflate, let last = s.latest, conflate(last, element) {
                return
            }
            if case .none = replay {} else {
                s.latest = element
            }
            for continuation in s.continuations.values {
                continuation.yield(element)
            }
        }
    }

    /// Kotlin `MutableSharedFlow.resetReplayCache()` parity: only future
    /// subscribers are affected; already-buffered deliveries stand.
    func clearReplay() {
        state.withLock { $0.latest = nil }
    }

    var latestValue: Element? {
        state.withLock { $0.latest }
    }

    func subscribe(replayLatest: Bool = true) -> AsyncStream<Element> {
        let id = UUID()
        let (stream, continuation) = AsyncStream.makeStream(of: Element.self)
        continuation.onTermination = { [weak self] _ in
            self?.remove(id)
        }
        state.withLock { s in
            if replayLatest, let latest = s.latest {
                continuation.yield(latest)
            }
            s.continuations[id] = continuation
        }
        return stream
    }

    private func remove(_ id: UUID) {
        state.withLock { _ = $0.continuations.removeValue(forKey: id) }
    }
}

extension Broadcaster where Element: Equatable {
    /// StateFlow parity: replay the latest value to new subscribers and skip
    /// emissions equal to the last published value (value conflation).
    static func stateFlow() -> Broadcaster<Element> {
        Broadcaster(replay: .latest, conflatingWhen: { $0 == $1 })
    }
}
