import XCTest
@testable import Mobaide

final class UserEventBusTests: XCTestCase {
    private struct BusHarness {
        let session: SessionHarness
        let transportFactory = FakeWebSocketTransportFactory()
        let agentStatus = RecordingAgentStatusApplier()
        let templateSync = RecordingTemplateSync()
        let shortcutSync = RecordingShortcutSync()
        let bus: UserEventBus

        init(session: SessionHarness) {
            self.session = session
            bus = UserEventBus(
                webSocketFactory: WebSocketCoreFactory.live(
                    transportFactory: transportFactory,
                    sleeper: RecordingSleeper()
                ),
                sessionManager: session.sessionManager,
                userIdentityStore: session.userIdentityStore,
                tokenStore: session.tokenStore,
                agentStatusApplier: agentStatus,
                templateSync: templateSync,
                shortcutSync: shortcutSync
            )
        }
    }

    // MARK: - Demux (pure, fixture-driven)

    func testTemplateUpsertFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/central/TemplateDto.json") {
            for type in ["template.created", "template.updated"] {
                let envelope = try EnvelopeFixtures.envelope(type: type, payload: fixtureCase.wire)
                guard case .templateUpserted = UserEnvelopeDemux.demux(envelope) else {
                    return XCTFail("case \(fixtureCase.name) (\(type)): expected templateUpserted")
                }
            }
        }
    }

    func testTemplateDeletedFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/central/TemplateDeletedPayload.json") {
            let envelope = try EnvelopeFixtures.envelope(type: "template.deleted", payload: fixtureCase.wire)
            guard case .templateDeleted(let templateId) = UserEnvelopeDemux.demux(envelope) else {
                return XCTFail("case \(fixtureCase.name): expected templateDeleted")
            }
            XCTAssertFalse(templateId.isEmpty)
        }
    }

    func testShortcutUpsertFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/central/ShortcutDto.json") {
            for type in ["shortcut.created", "shortcut.updated"] {
                let envelope = try EnvelopeFixtures.envelope(type: type, payload: fixtureCase.wire)
                guard case .shortcutUpserted(let shortcut) = UserEnvelopeDemux.demux(envelope) else {
                    return XCTFail("case \(fixtureCase.name) (\(type)): expected shortcutUpserted")
                }
                XCTAssertFalse(shortcut.shortcut_id.isEmpty)
            }
        }
    }

    func testShortcutUpsertDemuxPreservesUnknownKindVerbatim() {
        // `ShortcutDto.kind` is deliberately a raw String: decoding it as
        // `SessionKind` would coerce unknown kinds to .claude and re-bucket a
        // newer client's shortcut under Claude sessions.
        let action = UserEnvelopeDemux.demux(
            #"{"type":"shortcut.created","payload":{"shortcut_id":"ffeeddccbbaa","kind":"future_kind","chord_token":"ctrl+a","position":0,"created_at":"c","updated_at":"u","version":1}}"#
        )
        guard case .shortcutUpserted(let shortcut) = action else {
            return XCTFail("expected shortcutUpserted")
        }
        XCTAssertEqual(shortcut.kind, "future_kind")
    }

    func testShortcutDeletedFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/central/ShortcutDeletedPayload.json") {
            let envelope = try EnvelopeFixtures.envelope(type: "shortcut.deleted", payload: fixtureCase.wire)
            guard case .shortcutDeleted(let shortcutId) = UserEnvelopeDemux.demux(envelope) else {
                return XCTFail("case \(fixtureCase.name): expected shortcutDeleted")
            }
            XCTAssertFalse(shortcutId.isEmpty)
        }
    }

    func testMalformedShortcutPayloadsAreSilentlyIgnored() {
        XCTAssertEqual(
            UserEnvelopeDemux.demux(#"{"type":"shortcut.created","payload":{"shortcut_id":42}}"#),
            .none
        )
        XCTAssertEqual(
            UserEnvelopeDemux.demux(#"{"type":"shortcut.deleted","payload":{}}"#),
            .none
        )
    }

    func testAgentStatusChangedDemux() {
        let action = UserEnvelopeDemux.demux(
            #"{"type":"agent.status_changed","payload":{"agent_id":"A1","online":true}}"#
        )
        XCTAssertEqual(action, .agentStatusChanged(agentId: "A1", online: true))
    }

    func testUnknownAndMalformedAreSilentlyIgnored() {
        XCTAssertEqual(UserEnvelopeDemux.demux(#"{"type":"totally.new","payload":{}}"#), .none)
        XCTAssertEqual(UserEnvelopeDemux.demux("not json"), .none)
        XCTAssertEqual(
            UserEnvelopeDemux.demux(#"{"type":"agent.status_changed","payload":{"agent_id":42}}"#),
            .none,
            "undecodable payload is dropped"
        )
    }

    // MARK: - Lifecycle

    func testNoAutoStartWithoutExplicitStart() async {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true, agentConnected: true))
        // fullyConnected is the replayed value, but nothing subscribed —
        // the user bus reacts to changes only plus explicit start().
        let none = await harness.transportFactory.assertNoConnection(beyond: 0)
        XCTAssertTrue(none)
        _ = harness.bus
    }

    func testStartConnectsWhileUserOnly() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")

        await harness.bus.start()

        let connection = try await harness.transportFactory.connection(1)
        XCTAssertEqual(
            connection.url.absoluteString,
            "wss://agent.example.com/ws/user-events?token=user-token"
        )
    }

    func testStartWhileLoggedOutDoesNotConnectThenLoginChangeConnects() async throws {
        let harness = BusHarness(session: SessionHarness())
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")

        await harness.bus.start()
        let none = await harness.transportFactory.assertNoConnection(beyond: 0)
        XCTAssertTrue(none)

        harness.session.userIdentityStore.saveUserSession(
            accessToken: "user-token",
            email: "user@example.com",
            userId: "u1",
            role: "user"
        )
        await harness.session.sessionManager.markUserLoggedIn()

        _ = try await harness.transportFactory.connection(1)
    }

    func testStopsOnInvalidationAndIncompatibleServer() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let states = StreamCollector(harness.bus.connectionStates())

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        _ = try await states.next(where: { $0 == .connected })

        await harness.session.sessionManager.invalidateUserSession(.tokenRejected)
        _ = try await states.next(where: { $0 == .disconnected })
        let noNew = await harness.transportFactory.assertNoConnection(beyond: 1)
        XCTAssertTrue(noNew)
    }

    func testRelayModeUsesRelayBaseWithoutPathRewrite() async throws {
        let session = SessionHarness(userLoggedIn: true)
        session.tokenStore.saveRelayCredentials(
            serverUrl: "https://relay.example.com",
            agentId: "A1",
            pairingId: "P1",
            deviceId: "D1",
            accessToken: "a",
            refreshToken: "r"
        )
        let harness = BusHarness(session: session)

        await harness.bus.start()

        let connection = try await harness.transportFactory.connection(1)
        XCTAssertEqual(
            connection.url.absoluteString,
            "wss://relay.example.com/ws/user-events?token=user-token",
            "user channel is never relay-path-rewritten"
        )
    }

    // MARK: - Event handling (end-to-end)

    func testConnectedTriggersReconcileAndMessagesRouteToHooks() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let states = StreamCollector(harness.bus.connectionStates())

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        _ = try await states.next(where: { $0 == .connected })

        connection.emit(.text(#"{"type":"agent.status_changed","payload":{"agent_id":"A1","online":false}}"#))
        connection.emit(.text(
            #"{"type":"template.deleted","payload":{"template_id":"t1","version":4,"updated_at":"2026-07-06T09:00:00Z"}}"#
        ))

        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline,
              harness.templateSync.deletes.isEmpty || harness.agentStatus.updates.isEmpty {
            try await Task.sleep(for: .milliseconds(1))
        }
        XCTAssertEqual(harness.agentStatus.refreshCount, 1, "on-connect reconcile")
        XCTAssertEqual(harness.templateSync.refreshCount, 1, "on-connect reconcile")
        XCTAssertEqual(harness.agentStatus.updates.first?.agentId, "A1")
        XCTAssertEqual(harness.agentStatus.updates.first?.online, false)
        XCTAssertEqual(harness.templateSync.deletes, ["t1"])
    }

    func testShortcutEventsRouteToHooksAndReconcileRefreshes() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let states = StreamCollector(harness.bus.connectionStates())

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        _ = try await states.next(where: { $0 == .connected })

        connection.emit(.text(
            #"{"type":"shortcut.created","payload":{"shortcut_id":"sc1","kind":"claude","chord_token":"ctrl+a","position":0,"created_at":"c","updated_at":"u","version":1}}"#
        ))
        connection.emit(.text(
            #"{"type":"shortcut.deleted","payload":{"shortcut_id":"sc0","version":2,"updated_at":"u"}}"#
        ))

        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline,
              harness.shortcutSync.upserts.isEmpty || harness.shortcutSync.deletes.isEmpty {
            try await Task.sleep(for: .milliseconds(1))
        }
        XCTAssertEqual(harness.shortcutSync.refreshCount, 1, "on-connect reconcile re-pulls shortcuts too")
        XCTAssertEqual(harness.shortcutSync.upserts.map(\.shortcut_id), ["sc1"])
        XCTAssertEqual(harness.shortcutSync.deletes, ["sc0"])
    }

    func testLogoutClearsShortcutCache() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)

        await harness.session.sessionManager.invalidateUserSession(.tokenRejected)

        // Cross-account isolation: a different account signing in on this
        // process must never see the previous user's shortcut buttons.
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline, harness.shortcutSync.clearCount < 1 {
            try await Task.sleep(for: .milliseconds(1))
        }
        XCTAssertEqual(harness.shortcutSync.clearCount, 1)
    }

    func testTokenRejectedCloseInvalidatesSession() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let events = StreamCollector(harness.session.sessionManager.sessionEvents())

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        connection.emit(.closed(code: 4001, reason: "auth failed"))

        let event = try await events.next()
        XCTAssertEqual(event, .tokenRejected)
        let state = await harness.session.sessionManager.currentAuthState
        XCTAssertEqual(state, .unauthenticated)
        XCTAssertFalse(harness.session.userIdentityStore.isUserLoggedIn())
    }

    func testAccountRemovedCloseInvalidatesWithAccountRemoved() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let events = StreamCollector(harness.session.sessionManager.sessionEvents())

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        connection.emit(.closed(code: 4010, reason: "admin revoked"))

        let event = try await events.next()
        XCTAssertEqual(event, .accountRemoved)
    }

    func testIncompatibleServerCloseMarksTakeoverState() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let states = StreamCollector(harness.session.sessionManager.authStates())

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        connection.emit(.closed(code: 4002, reason: "protocol v9 required"))

        let state = try await states.next(where: {
            if case .incompatibleServer = $0 { return true }
            return false
        })
        guard case .incompatibleServer(let info) = state else {
            return XCTFail("expected incompatibleServer")
        }
        XCTAssertEqual(info.reason, "protocol v9 required")
    }
}
