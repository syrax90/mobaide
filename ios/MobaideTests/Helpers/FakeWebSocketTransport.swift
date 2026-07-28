import Foundation
@testable import Mobaide

/// Scripted transport: tests drive each connection by emitting transport
/// events manually and record everything the core sends or closes.
final class FakeWebSocketConnection: WebSocketTransportConnection, @unchecked Sendable {
    let url: URL
    let events: AsyncStream<WebSocketTransportEvent>

    private let continuation: AsyncStream<WebSocketTransportEvent>.Continuation
    private let lock = NSLock()
    private var startedFlag = false
    private var sentTextsStorage: [String] = []
    private var sentBinariesStorage: [Data] = []
    private var droppedBinariesStorage: [Data] = []
    private var dropBinariesFlag = false
    private var closeCallsStorage: [(code: UInt16, reason: String)] = []
    private var cancelled = false

    init(url: URL) {
        self.url = url
        (events, continuation) = AsyncStream.makeStream(of: WebSocketTransportEvent.self)
    }

    var started: Bool { lock.withLock { startedFlag } }
    var sentTexts: [String] { lock.withLock { sentTextsStorage } }
    var sentBinaries: [Data] { lock.withLock { sentBinariesStorage } }
    var closeCalls: [(code: UInt16, reason: String)] { lock.withLock { closeCallsStorage } }
    var wasCancelled: Bool { lock.withLock { cancelled } }

    /// While true, `send(binary:)` fails and the frame never lands in
    /// `sentBinaries` — the real transport's silent drop over a socket that is
    /// down. Dropped frames are kept in `droppedBinaries` so a test can wait
    /// for the drop instead of racing a debounce timer.
    var dropsBinaries: Bool {
        get { lock.withLock { dropBinariesFlag } }
        set { lock.withLock { dropBinariesFlag = newValue } }
    }

    var droppedBinaries: [Data] { lock.withLock { droppedBinariesStorage } }

    func start() {
        lock.withLock { startedFlag = true }
    }

    func send(text: String) async -> Bool {
        lock.withLock { sentTextsStorage.append(text) }
        return true
    }

    func send(binary: Data) async -> Bool {
        lock.withLock { () -> Bool in
            if dropBinariesFlag {
                droppedBinariesStorage.append(binary)
                return false
            }
            sentBinariesStorage.append(binary)
            return true
        }
    }

    func close(code: UInt16, reason: String) {
        lock.withLock { closeCallsStorage.append((code, reason)) }
    }

    func cancel() {
        lock.withLock { cancelled = true }
    }

    func emit(_ event: WebSocketTransportEvent) {
        continuation.yield(event)
    }

    func finishEvents() {
        continuation.finish()
    }
}

final class FakeWebSocketTransportFactory: WebSocketTransportFactory, @unchecked Sendable {
    private let lock = NSLock()
    private var connectionsStorage: [FakeWebSocketConnection] = []

    var connections: [FakeWebSocketConnection] {
        lock.withLock { connectionsStorage }
    }

    func makeConnection(url: URL) -> any WebSocketTransportConnection {
        let connection = FakeWebSocketConnection(url: url)
        lock.withLock { connectionsStorage.append(connection) }
        return connection
    }

    /// Waits until at least `count` connections exist and the newest one has
    /// been started, then returns it. Fails the calling test on timeout.
    func connection(_ count: Int, timeout: Duration = .seconds(5)) async throws -> FakeWebSocketConnection {
        let deadline = ContinuousClock.now.advanced(by: timeout)
        while ContinuousClock.now < deadline {
            let snapshot = connections
            if snapshot.count >= count, snapshot[count - 1].started {
                return snapshot[count - 1]
            }
            try await Task.sleep(for: .milliseconds(1))
        }
        throw FakeTransportTimeout(expectedCount: count, actualCount: connections.count)
    }

    /// Gives in-flight core tasks a chance to run, then asserts no connection
    /// beyond `count` was ever created (used for "reconnect suppressed" checks).
    func assertNoConnection(beyond count: Int) async -> Bool {
        try? await Task.sleep(for: .milliseconds(50))
        return connections.count <= count
    }
}

struct FakeTransportTimeout: Error {
    let expectedCount: Int
    let actualCount: Int
}
