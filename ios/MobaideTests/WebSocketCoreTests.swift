import XCTest
@testable import Mobaide

final class WebSocketCoreTests: XCTestCase {
    private final class AuthFlag: @unchecked Sendable {
        private let lock = NSLock()
        private var value: Bool
        init(_ value: Bool) { self.value = value }
        var isAuthenticated: Bool {
            get { lock.withLock { value } }
            set { lock.withLock { value = newValue } }
        }
    }

    private final class URLCounter: @unchecked Sendable {
        private let lock = NSLock()
        private var count = 0
        func nextURL() -> String {
            lock.withLock {
                count += 1
                return "ws://host/ws/events?attempt=\(count)"
            }
        }
    }

    /// A URL lookup that stays suspended until the test opens it, so a test can
    /// run other core calls while a connect waits inside `urlProvider`.
    private final class URLGate: @unchecked Sendable {
        private let lock = NSLock()
        private var isOpen = false
        private var passing: Int
        private var callCount = 0
        private var waiters: [CheckedContinuation<Void, Never>] = []

        /// The first `passing` lookups return at once.
        init(passing: Int = 0) { self.passing = passing }

        var calls: Int { lock.withLock { callCount } }

        func url() async -> String? {
            await withCheckedContinuation { continuation in
                let resumeNow = lock.withLock { () -> Bool in
                    callCount += 1
                    if isOpen || callCount <= passing { return true }
                    waiters.append(continuation)
                    return false
                }
                if resumeNow { continuation.resume() }
            }
            return "ws://host/ws/events?token=t"
        }

        func open() {
            let pending = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
                isOpen = true
                defer { waiters.removeAll() }
                return waiters
            }
            pending.forEach { $0.resume() }
        }

        func waitForCalls(_ count: Int, timeout: Duration = .seconds(5)) async throws {
            let deadline = ContinuousClock.now.advanced(by: timeout)
            while calls < count {
                guard ContinuousClock.now < deadline else { throw CollectorTimeout() }
                try await Task.sleep(for: .milliseconds(1))
            }
        }
    }

    private func makeCore(
        factory: FakeWebSocketTransportFactory,
        sleeper: RecordingSleeper = RecordingSleeper(),
        auth: AuthFlag = AuthFlag(true),
        url: @escaping @Sendable () async throws -> String? = { "ws://host/ws/events?token=t" }
    ) -> (WebSocketCore, EventCollector) {
        let core = WebSocketCore(
            urlProvider: url,
            transportFactory: factory,
            isAuthenticated: { auth.isAuthenticated },
            sleeper: sleeper
        )
        let collector = EventCollector()
        collector.start(core.events)
        return (core, collector)
    }

    func testBackoffDoublesFromOneSecondToThirtySecondCap() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let (core, _) = makeCore(factory: factory, sleeper: sleeper)
        await core.connect()

        for attempt in 1...7 {
            let connection = try await factory.connection(attempt)
            connection.emit(.closed(code: 1006, reason: ""))
        }
        _ = try await factory.connection(8)

        XCTAssertEqual(sleeper.durations, [
            .milliseconds(1000),
            .milliseconds(2000),
            .milliseconds(4000),
            .milliseconds(8000),
            .milliseconds(16000),
            .milliseconds(30000),
            .milliseconds(30000),
        ])
    }

    func testBackoffResetsOnSuccessfulOpen() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let (core, _) = makeCore(factory: factory, sleeper: sleeper)
        await core.connect()

        let first = try await factory.connection(1)
        first.emit(.closed(code: 1006, reason: ""))

        let second = try await factory.connection(2)
        second.emit(.opened)
        second.emit(.closed(code: 1006, reason: ""))

        _ = try await factory.connection(3)
        XCTAssertEqual(sleeper.durations, [.milliseconds(1000), .milliseconds(1000)])
    }

    func testStaleGenerationEventsAreDroppedAfterForceReconnect() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let (core, collector) = makeCore(factory: factory, sleeper: sleeper)
        await core.connect()

        let first = try await factory.connection(1)
        first.emit(.opened)
        let opened = try await collector.next()
        XCTAssertEqual(opened, .connected)

        await core.forceReconnect()
        XCTAssertEqual(first.closeCalls.map(\.code), [1000])
        XCTAssertEqual(first.closeCalls.map(\.reason), ["Force reconnect"])

        first.emit(.closed(code: 1000, reason: ""))

        let second = try await factory.connection(2)
        second.emit(.opened)
        let reopened = try await collector.next()
        XCTAssertEqual(reopened, .connected)

        let clean = await collector.noPendingEvents()
        XCTAssertTrue(clean, "stale close must not emit events: \(collector.all)")
        XCTAssertEqual(sleeper.durations, [], "stale close must not schedule a reconnect")
    }

    func testClose4001EmitsDisconnectedThenTokenRejectedAndStops() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let (core, collector) = makeCore(factory: factory, sleeper: sleeper)
        await core.connect()

        let connection = try await factory.connection(1)
        connection.emit(.closed(code: 4001, reason: ""))

        let firstEvent = try await collector.next()
        let secondEvent = try await collector.next()
        XCTAssertEqual(firstEvent, .disconnected)
        XCTAssertEqual(secondEvent, .tokenRejected)

        let suppressed = await factory.assertNoConnection(beyond: 1)
        XCTAssertTrue(suppressed)
        XCTAssertEqual(sleeper.durations, [])
    }

    func testClose4010EmitsAccountRemovedAndStops() async throws {
        let factory = FakeWebSocketTransportFactory()
        let (core, collector) = makeCore(factory: factory)
        await core.connect()

        let connection = try await factory.connection(1)
        connection.emit(.closed(code: 4010, reason: ""))

        let firstEvent = try await collector.next()
        let secondEvent = try await collector.next()
        XCTAssertEqual(firstEvent, .disconnected)
        XCTAssertEqual(secondEvent, .accountRemoved)

        let suppressed = await factory.assertNoConnection(beyond: 1)
        XCTAssertTrue(suppressed)
    }

    func testClose4002UsesReasonOrDefaultAndStops() async throws {
        let factory = FakeWebSocketTransportFactory()
        let (core, collector) = makeCore(factory: factory)
        await core.connect()

        let connection = try await factory.connection(1)
        connection.emit(.closed(code: 4002, reason: "please upgrade"))

        _ = try await collector.next()
        let terminal = try await collector.next()
        XCTAssertEqual(terminal, .incompatibleServer(reason: "please upgrade"))

        let suppressed = await factory.assertNoConnection(beyond: 1)
        XCTAssertTrue(suppressed)
    }

    func testCodes4011And4012FallThroughToReconnect() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let (core, _) = makeCore(factory: factory, sleeper: sleeper)
        await core.connect()

        let first = try await factory.connection(1)
        first.emit(.closed(code: 4011, reason: "admin force disconnect"))
        let second = try await factory.connection(2)
        second.emit(.closed(code: 4012, reason: "agent offline"))
        _ = try await factory.connection(3)

        XCTAssertEqual(sleeper.durations.count, 2)
    }

    func testUpgradeRejected401IsTreatedAsTokenRejected() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let (core, collector) = makeCore(factory: factory, sleeper: sleeper)
        await core.connect()

        let connection = try await factory.connection(1)
        connection.emit(.failed(.upgradeRejected(statusCode: 401)))

        let errorEvent = try await collector.next()
        guard case .error = errorEvent else {
            return XCTFail("expected .error first, got \(errorEvent)")
        }
        let disconnected = try await collector.next()
        let terminal = try await collector.next()
        XCTAssertEqual(disconnected, .disconnected)
        XCTAssertEqual(terminal, .tokenRejected)

        let suppressed = await factory.assertNoConnection(beyond: 1)
        XCTAssertTrue(suppressed)
        XCTAssertEqual(sleeper.durations, [])
    }

    func testTransportFailureSchedulesReconnect() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let (core, collector) = makeCore(factory: factory, sleeper: sleeper)
        await core.connect()

        let connection = try await factory.connection(1)
        connection.emit(.failed(.network("connection reset")))

        _ = try await collector.next()
        let disconnected = try await collector.next()
        XCTAssertEqual(disconnected, .disconnected)

        _ = try await factory.connection(2)
        XCTAssertEqual(sleeper.durations, [.milliseconds(1000)])
    }

    func testUnauthenticatedCloseDoesNotReconnect() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let auth = AuthFlag(true)
        let (core, _) = makeCore(factory: factory, sleeper: sleeper, auth: auth)
        await core.connect()

        let connection = try await factory.connection(1)
        auth.isAuthenticated = false
        connection.emit(.closed(code: 1006, reason: ""))

        let suppressed = await factory.assertNoConnection(beyond: 1)
        XCTAssertTrue(suppressed)
        XCTAssertEqual(sleeper.durations, [])
    }

    func testURLProviderIsReEvaluatedOnEveryAttempt() async throws {
        let factory = FakeWebSocketTransportFactory()
        let counter = URLCounter()
        let (core, _) = makeCore(factory: factory, url: { counter.nextURL() })
        await core.connect()

        let first = try await factory.connection(1)
        XCTAssertEqual(first.url.absoluteString, "ws://host/ws/events?attempt=1")
        first.emit(.closed(code: 1006, reason: ""))

        let second = try await factory.connection(2)
        XCTAssertEqual(second.url.absoluteString, "ws://host/ws/events?attempt=2")
    }

    func testNilURLEmitsDisconnectedWithoutConnecting() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let (core, collector) = makeCore(factory: factory, sleeper: sleeper, url: { nil })
        await core.connect()

        let event = try await collector.next()
        XCTAssertEqual(event, .disconnected)
        XCTAssertTrue(factory.connections.isEmpty)
        XCTAssertEqual(sleeper.durations, [])
    }

    func testDisconnectClosesWithNormalCodeAndStopsReconnect() async throws {
        let factory = FakeWebSocketTransportFactory()
        let sleeper = RecordingSleeper()
        let (core, collector) = makeCore(factory: factory, sleeper: sleeper)
        await core.connect()

        let connection = try await factory.connection(1)
        connection.emit(.opened)
        _ = try await collector.next()

        await core.disconnect()
        XCTAssertEqual(connection.closeCalls.map(\.code), [1000])
        XCTAssertEqual(connection.closeCalls.map(\.reason), ["Client disconnect"])

        let suppressed = await factory.assertNoConnection(beyond: 1)
        XCTAssertTrue(suppressed)
    }

    func testDisconnectDuringASlowURLLookupOpensNoSocket() async throws {
        let factory = FakeWebSocketTransportFactory()
        let gate = URLGate()
        let (core, collector) = makeCore(factory: factory, url: { await gate.url() })

        let connecting = Task { await core.connect() }
        try await gate.waitForCalls(1)
        await core.disconnect()
        gate.open()
        await connecting.value

        XCTAssertTrue(factory.connections.isEmpty, "a connect that lost to a disconnect must not open a socket")
        let clean = await collector.noPendingEvents()
        XCTAssertTrue(clean, "a cancelled connect must not emit events: \(collector.all)")
    }

    func testConcurrentForceReconnectsOpenOneSocket() async throws {
        let factory = FakeWebSocketTransportFactory()
        let gate = URLGate(passing: 1)
        let (core, collector) = makeCore(factory: factory, url: { await gate.url() })
        await core.connect()
        let first = try await factory.connection(1)
        first.emit(.opened)
        let opened = try await collector.next()
        XCTAssertEqual(opened, .connected)

        // Both reconnects wait in the URL lookup before either opens a socket.
        let older = Task { await core.forceReconnect() }
        try await gate.waitForCalls(2)
        let newer = Task { await core.forceReconnect() }
        try await gate.waitForCalls(3)
        gate.open()
        await older.value
        await newer.value

        let single = await factory.assertNoConnection(beyond: 2)
        XCTAssertTrue(single, "the superseded reconnect must not open a second socket: \(factory.connections.count)")
        XCTAssertEqual(first.closeCalls.map(\.reason), ["Force reconnect"])
        let second = try await factory.connection(2)
        second.emit(.opened)
        let reopened = try await collector.next()
        XCTAssertEqual(reopened, .connected)
        let clean = await collector.noPendingEvents()
        XCTAssertTrue(clean, "one socket means one set of events: \(collector.all)")
    }

    func testSendWithoutConnectionEmitsErrorAndReturnsFalse() async throws {
        let factory = FakeWebSocketTransportFactory()
        let (core, collector) = makeCore(factory: factory)

        let sent = await core.send(text: "hello")
        XCTAssertFalse(sent)
        let event = try await collector.next()
        XCTAssertEqual(event, .error("WebSocket send failed"))
    }
}
