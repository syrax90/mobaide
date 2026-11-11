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

    func testSendWithoutConnectionEmitsErrorAndReturnsFalse() async throws {
        let factory = FakeWebSocketTransportFactory()
        let (core, collector) = makeCore(factory: factory)

        let sent = await core.send(text: "hello")
        XCTAssertFalse(sent)
        let event = try await collector.next()
        XCTAssertEqual(event, .error("WebSocket send failed"))
    }
}
