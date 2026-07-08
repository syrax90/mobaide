import XCTest
@testable import Mobaide

final class AgentEventBusTests: XCTestCase {
    private func makeBus(
        harness: SessionHarness,
        transportFactory: FakeWebSocketTransportFactory
    ) -> AgentEventBus {
        AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(
                transportFactory: transportFactory,
                sleeper: RecordingSleeper()
            ),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
    }

    // MARK: - Demux (pure, fixture-driven)

    func testSessionsChangedFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/agent/SessionResponse.json") {
            let listEnvelope = try EnvelopeFixtures.envelope(
                type: "sessions.changed",
                payload: .object(["sessions": .array([fixtureCase.wire])])
            )
            guard case .sessionsChanged(let sessions)? = AgentEnvelopeDemux.demux(listEnvelope) else {
                return XCTFail("case \(fixtureCase.name): expected sessionsChanged")
            }
            XCTAssertEqual(sessions.count, 1, "case \(fixtureCase.name)")

            let updatedEnvelope = try EnvelopeFixtures.envelope(
                type: "session.updated",
                payload: .object(["session": fixtureCase.wire])
            )
            guard case .sessionUpdated(let updated)? = AgentEnvelopeDemux.demux(updatedEnvelope) else {
                return XCTFail("case \(fixtureCase.name): expected sessionUpdated")
            }
            XCTAssertEqual(updated, sessions[0], "case \(fixtureCase.name)")

            let attentionEnvelope = try EnvelopeFixtures.envelope(
                type: "session.attention",
                payload: .object(["session": fixtureCase.wire])
            )
            guard case .sessionAttention? = AgentEnvelopeDemux.demux(attentionEnvelope) else {
                return XCTFail("case \(fixtureCase.name): expected sessionAttention")
            }
        }
    }

    func testProjectsChangedFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/agent/ProjectInfo.json") {
            let envelope = try EnvelopeFixtures.envelope(
                type: "projects.changed",
                payload: .object(["projects": .array([fixtureCase.wire])])
            )
            guard case .projectsChanged(let projects)? = AgentEnvelopeDemux.demux(envelope) else {
                return XCTFail("case \(fixtureCase.name): expected projectsChanged")
            }
            XCTAssertEqual(projects.count, 1, "case \(fixtureCase.name)")
        }
    }

    func testAutoScanStatusFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/agent/AutoScanInstanceDto.json") {
            let envelope = try EnvelopeFixtures.envelope(type: "autoscan.status", payload: fixtureCase.wire)
            guard case .autoScanStatus? = AgentEnvelopeDemux.demux(envelope) else {
                return XCTFail("case \(fixtureCase.name): expected autoScanStatus")
            }
        }
    }

    func testAutoScanListChangedFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/agent/AutoScanListResponse.json") {
            let envelope = try EnvelopeFixtures.envelope(type: "autoscan.list.changed", payload: fixtureCase.wire)
            guard case .autoScanListChanged? = AgentEnvelopeDemux.demux(envelope) else {
                return XCTFail("case \(fixtureCase.name): expected autoScanListChanged")
            }
        }
    }

    func testGitChangedDemux() {
        let event = AgentEnvelopeDemux.demux(
            #"{"type":"git.changed","payload":{"project_name":"demo"}}"#
        )
        XCTAssertEqual(event, .gitChanged(projectName: "demo"))

        let missingName = AgentEnvelopeDemux.demux(
            #"{"type":"git.changed","payload":{}}"#
        )
        XCTAssertEqual(missingName, .gitChanged(projectName: ""))
    }

    func testAutoScanEventDemux() {
        let text = """
        {"type":"autoscan.event","payload":{"project_name":"demo","event":\
        {"seq":9007199254740993,"ts":"2026-07-06T10:00:00Z","type":"phase","payload":{"phase":"scan_started"}}}}
        """
        guard case .autoScanEvent(let projectName, let seq, let ts, let payload)? = AgentEnvelopeDemux.demux(text) else {
            return XCTFail("expected autoScanEvent")
        }
        XCTAssertEqual(projectName, "demo")
        XCTAssertEqual(seq, 9_007_199_254_740_993, "seq is 64-bit, beyond 2^53")
        XCTAssertEqual(ts, "2026-07-06T10:00:00Z")
        XCTAssertEqual(payload, .phase(phase: "scan_started", cycleNumber: 0, nextScanAt: nil, issuesProcessed: 0))
    }

    func testAutoScanEventWithoutEventObjectIsDropped() {
        let event = AgentEnvelopeDemux.demux(
            #"{"type":"autoscan.event","payload":{"project_name":"demo","event":"nope"}}"#
        )
        XCTAssertNil(event)
    }

    func testUnknownTypeBecomesUnknownEnvelopeType() {
        let raw = #"{"type":"totally.new","payload":{}}"#
        XCTAssertEqual(
            AgentEnvelopeDemux.demux(raw),
            .unknownEnvelopeType(type: "totally.new", raw: raw)
        )
    }

    func testMalformedAndUndecodableMessagesAreDropped() {
        XCTAssertNil(AgentEnvelopeDemux.demux("not json"))
        XCTAssertNil(AgentEnvelopeDemux.demux(#"{"payload":{}}"#), "missing type")
        XCTAssertNil(AgentEnvelopeDemux.demux(#"{"type":"sessions.changed"}"#), "missing payload")
        XCTAssertNil(
            AgentEnvelopeDemux.demux(#"{"type":"sessions.changed","payload":{"sessions":[{"bogus":1}]}}"#),
            "payload failing type-specific decode is dropped"
        )
    }

    // MARK: - Lifecycle + broadcast (end-to-end over the fake transport)

    func testStartsOnReplayedFullyConnectedAndBroadcastsToTwoSubscribers() async throws {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let transportFactory = FakeWebSocketTransportFactory()
        let bus = makeBus(harness: harness, transportFactory: transportFactory)

        let first = StreamCollector(bus.events())
        let second = StreamCollector(bus.events())
        await bus.start()

        let connection = try await transportFactory.connection(1)
        XCTAssertTrue(connection.url.absoluteString.hasPrefix("wss://agent.example.com/ws/events?token="))

        // Initial burst immediately after open must not be lost
        // (consumer attaches before connect).
        connection.emit(.opened)
        connection.emit(.text(#"{"type":"git.changed","payload":{"project_name":"p1"}}"#))
        connection.emit(.text(#"{"type":"unknown.kind","payload":{}}"#))

        let firstGitEvent = try await first.next()
        XCTAssertEqual(firstGitEvent, .gitChanged(projectName: "p1"))
        guard case .unknownEnvelopeType(let type, _) = try await first.next() else {
            return XCTFail("expected unknownEnvelopeType")
        }
        XCTAssertEqual(type, "unknown.kind")

        let secondGitEvent = try await second.next()
        XCTAssertEqual(secondGitEvent, .gitChanged(projectName: "p1"))
        guard case .unknownEnvelopeType = try await second.next() else {
            return XCTFail("second subscriber must receive every event too")
        }
        let count = await bus.unknownEnvelopeCount
        XCTAssertEqual(count, 1)
    }

    func testDoesNotStartWhenNotFullyConnected() async {
        let harness = SessionHarness(userLoggedIn: true)
        let transportFactory = FakeWebSocketTransportFactory()
        let bus = makeBus(harness: harness, transportFactory: transportFactory)

        await bus.start()

        let none = await transportFactory.assertNoConnection(beyond: 0)
        XCTAssertTrue(none)
    }

    func testStartsOnTransitionToFullyConnectedAndStopsOnDemotion() async throws {
        let harness = SessionHarness(userLoggedIn: true)
        harness.tokenStore.saveAgentUrl("https://agent.example.com")
        let transportFactory = FakeWebSocketTransportFactory()
        let bus = makeBus(harness: harness, transportFactory: transportFactory)
        let states = StreamCollector(bus.connectionStates())

        await bus.start()
        harness.tokenStore.saveTokens(
            accessToken: TestJWT.token(exp: Int64(Date().timeIntervalSince1970) + 3600),
            refreshToken: "r"
        )
        await harness.sessionManager.markAgentConnected()

        let connection = try await transportFactory.connection(1)
        connection.emit(.opened)
        _ = try await states.next(where: { $0 == .connected })

        await harness.sessionManager.agentDisconnect()
        _ = try await states.next(where: { $0 == .disconnected })
        let noNew = await transportFactory.assertNoConnection(beyond: 1)
        XCTAssertTrue(noNew, "demotion must not reconnect")
    }

    func testPreConnectTerminalRefreshAbandonsConnection() async {
        let now = Date()
        let transport = FakeRefreshTransport(behavior: .respond(status: 401, body: ""))
        let harness = SessionHarness(
            userLoggedIn: true,
            agentConnected: true,
            accessTokenExp: Int64(now.timeIntervalSince1970) + 10,
            transport: transport,
            now: now
        )
        let transportFactory = FakeWebSocketTransportFactory()
        let bus = makeBus(harness: harness, transportFactory: transportFactory)

        await bus.start()

        // The expiring token forces a pre-connect refresh; 401 is terminal,
        // so no WebSocket transport connection may ever be attempted.
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertGreaterThanOrEqual(transport.callCount, 1)
        let none = await transportFactory.assertNoConnection(beyond: 0)
        XCTAssertTrue(none)
    }

    func testRelayModeRewritesEventsPath() async throws {
        let harness = SessionHarness(userLoggedIn: true)
        harness.tokenStore.saveRelayCredentials(
            serverUrl: "https://relay.example.com",
            agentId: "A1",
            pairingId: "P1",
            deviceId: "D1",
            accessToken: TestJWT.token(exp: Int64(Date().timeIntervalSince1970) + 3600),
            refreshToken: "r"
        )
        let transportFactory = FakeWebSocketTransportFactory()
        let bus = makeBus(harness: harness, transportFactory: transportFactory)

        await bus.start()
        await harness.sessionManager.markAgentConnected()

        let connection = try await transportFactory.connection(1)
        XCTAssertTrue(
            connection.url.absoluteString.hasPrefix("wss://relay.example.com/relay/ws/events/A1?token="),
            "got \(connection.url.absoluteString)"
        )
    }
}
