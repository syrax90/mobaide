import XCTest
@testable import Mobaide

final class UserEventBusTests: XCTestCase {
    private struct BusHarness {
        let session: SessionHarness
        let transportFactory = FakeWebSocketTransportFactory()
        let deviceIdentityStore = DeviceIdentityStore(keychain: InMemoryKeychainStore())
        let agentStatus = RecordingAgentStatusApplier()
        let templateSync = RecordingTemplateSync()
        let shortcutSync = RecordingShortcutSync()
        let customTypeSync = RecordingCustomTypeSync()
        let conversion = RecordingFirstAgentPairedApplier()
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
                deviceIdentityStore: deviceIdentityStore,
                agentStatusApplier: agentStatus,
                templateSync: templateSync,
                shortcutSync: shortcutSync,
                customTypeSync: customTypeSync,
                conversion: conversion
            )
        }
    }

    /// One object wearing both sync hats, recording every call into a single
    /// ordered log — the only way to assert cross-hook ordering (type delete
    /// strictly before the shortcut-kind purge).
    private final class OrderedCascadeRecorder: ShortcutSyncApplying, CustomTypeSyncApplying, @unchecked Sendable {
        private let lock = NSLock()
        private var eventsStorage: [String] = []
        var events: [String] { lock.withLock { eventsStorage } }
        private func record(_ event: String) { lock.withLock { eventsStorage.append(event) } }

        func applyUpserted(_ shortcut: ShortcutDto) async { record("shortcutUpsert:\(shortcut.shortcut_id)") }
        func applyDeleted(shortcutId: String) async { record("shortcutDelete:\(shortcutId)") }
        func purgeKind(_ kind: String) async { record("purgeKind:\(kind)") }
        func refreshShortcuts() async throws { record("refreshShortcuts") }
        func applyUpserted(_ type: CustomAgentTypeDto) async { record("typeUpsert:\(type.id)") }
        func applyDeleted(typeId: String) async { record("typeDelete:\(typeId)") }
        func refreshCustomTypes() async throws { record("refreshCustomTypes") }
        func applyModeOrder(_ order: SessionModeOrderDto) async { record("modeOrder:\(order.version)") }
        func refreshModeOrder() async throws { record("refreshModeOrder") }
        func clear() async { record("clear") }
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

    func testCustomTypeUpsertFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/central/CustomAgentTypeDto.json") {
            for type in ["custom_type.created", "custom_type.updated"] {
                let envelope = try EnvelopeFixtures.envelope(type: type, payload: fixtureCase.wire)
                guard case .customTypeUpserted(let customType) = UserEnvelopeDemux.demux(envelope) else {
                    return XCTFail("case \(fixtureCase.name) (\(type)): expected customTypeUpserted")
                }
                XCTAssertFalse(customType.id.isEmpty)
            }
        }
    }

    func testCustomTypeDeletedFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/central/CustomTypeDeletedPayload.json") {
            let envelope = try EnvelopeFixtures.envelope(type: "custom_type.deleted", payload: fixtureCase.wire)
            guard case .customTypeDeleted(let typeId) = UserEnvelopeDemux.demux(envelope) else {
                return XCTFail("case \(fixtureCase.name): expected customTypeDeleted")
            }
            XCTAssertFalse(typeId.isEmpty)
        }
    }

    func testMalformedCustomTypePayloadsAreSilentlyIgnored() {
        XCTAssertEqual(
            UserEnvelopeDemux.demux(#"{"type":"custom_type.created","payload":{"id":42}}"#),
            .none
        )
        XCTAssertEqual(
            UserEnvelopeDemux.demux(#"{"type":"custom_type.deleted","payload":{}}"#),
            .none
        )
        XCTAssertEqual(
            UserEnvelopeDemux.demux(#"{"type":"custom_type.deleted","payload":{"type_id":"ab12cd34ef56"}}"#),
            .none,
            "the tombstone key is `id` — a `type_id` payload must not demux"
        )
    }

    func testAgentStatusChangedDemux() {
        let action = UserEnvelopeDemux.demux(
            #"{"type":"agent.status_changed","payload":{"agent_id":"A1","online":true}}"#
        )
        XCTAssertEqual(action, .agentStatusChanged(agentId: "A1", online: true))
    }

    func testFirstAgentPairedFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/central/FirstAgentPairedPayload.json") {
            let envelope = try EnvelopeFixtures.envelope(type: "account.first_agent_paired", payload: fixtureCase.wire)
            XCTAssertEqual(
                UserEnvelopeDemux.demux(envelope),
                .firstAgentPaired(agentId: "a1b2c3d4e5f6"),
                fixtureCase.name
            )
        }
        XCTAssertEqual(
            UserEnvelopeDemux.demux(#"{"type":"account.first_agent_paired","payload":{}}"#),
            .none
        )
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

    func testStartConnectsWhileUserOnly() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")

        await harness.bus.start()

        let connection = try await harness.transportFactory.connection(1)
        // The device id tells the relay which device this socket delivers to,
        // so an attention push skips it and still reaches the account's others.
        XCTAssertEqual(
            connection.url.absoluteString,
            "wss://agent.example.com/ws/user-events?token=user-token"
                + "&device_id=\(harness.deviceIdentityStore.getOrCreateDeviceId())"
        )
    }

    func testStartWhileLoggedOutDoesNotConnectThenLoginChangeConnects() async throws {
        let harness = BusHarness(session: SessionHarness())
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")

        await harness.bus.start()
        let none = await harness.transportFactory.assertNoConnection(beyond: 0)
        XCTAssertTrue(none)

        let identity = harness.session.userIdentityStore
        await harness.session.sessionManager.markUserLoggedIn(userId: "u1") {
            identity.saveUserSession(
                accessToken: "user-token",
                email: "user@example.com",
                userId: "u1",
                role: "user"
            )
        }

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
            "wss://relay.example.com/ws/user-events?token=user-token"
                + "&device_id=\(harness.deviceIdentityStore.getOrCreateDeviceId())",
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

    func testFirstAgentPairedRoutesToTheConversionAndConnectReconcilesIt() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let states = StreamCollector(harness.bus.connectionStates())

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        _ = try await states.next(where: { $0 == .connected })

        connection.emit(.text(#"{"type":"account.first_agent_paired","payload":{"agent_id":"A1"}}"#))

        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline, harness.conversion.paired.isEmpty {
            try await Task.sleep(for: .milliseconds(1))
        }
        XCTAssertEqual(harness.conversion.reconcileCount, 1, "on-connect reconcile reaches the conversion")
        XCTAssertEqual(harness.conversion.paired, ["A1"])
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

    func testCustomTypeEventsRouteToHooksAndReconcileRefreshes() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let states = StreamCollector(harness.bus.connectionStates())

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        _ = try await states.next(where: { $0 == .connected })

        connection.emit(.text(
            #"{"type":"custom_type.created","payload":{"id":"ab12cd34ef56","name":"Aider","command":"aider --model sonnet","created_at":"c","updated_at":"u"}}"#
        ))
        connection.emit(.text(
            #"{"type":"custom_type.deleted","payload":{"id":"ffffffffffff"}}"#
        ))

        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline,
              harness.customTypeSync.upserts.isEmpty || harness.shortcutSync.purges.isEmpty {
            try await Task.sleep(for: .milliseconds(1))
        }
        XCTAssertEqual(harness.customTypeSync.refreshCount, 1, "on-connect reconcile re-pulls custom types too")
        XCTAssertEqual(harness.customTypeSync.upserts.map(\.id), ["ab12cd34ef56"])
        XCTAssertEqual(harness.customTypeSync.deletes, ["ffffffffffff"])
        XCTAssertEqual(
            harness.shortcutSync.purges, ["custom_ffffffffffff"],
            "the deleted type's shortcut bucket is purged by full wire kind — the server cascade emits no per-shortcut events"
        )
    }

    func testCustomTypeDeletedAppliesDeleteBeforeShortcutPurge() async throws {
        // The purge keys on the type that just died; running it first would
        // let a concurrent upsert-for-that-kind sneak back in between.
        let session = SessionHarness(userLoggedIn: true)
        session.tokenStore.saveAgentUrl("https://agent.example.com")
        let transportFactory = FakeWebSocketTransportFactory()
        let recorder = OrderedCascadeRecorder()
        let bus = UserEventBus(
            webSocketFactory: WebSocketCoreFactory.live(
                transportFactory: transportFactory,
                sleeper: RecordingSleeper()
            ),
            sessionManager: session.sessionManager,
            userIdentityStore: session.userIdentityStore,
            tokenStore: session.tokenStore,
            deviceIdentityStore: DeviceIdentityStore(keychain: InMemoryKeychainStore()),
            shortcutSync: recorder,
            customTypeSync: recorder
        )

        await bus.start()
        let connection = try await transportFactory.connection(1)
        connection.emit(.opened)
        connection.emit(.text(#"{"type":"custom_type.deleted","payload":{"id":"ab12cd34ef56"}}"#))

        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline,
              !recorder.events.contains("purgeKind:custom_ab12cd34ef56") {
            try await Task.sleep(for: .milliseconds(1))
        }
        let cascade = recorder.events.filter { $0.hasPrefix("typeDelete:") || $0.hasPrefix("purgeKind:") }
        XCTAssertEqual(
            cascade, ["typeDelete:ab12cd34ef56", "purgeKind:custom_ab12cd34ef56"],
            "the type row is removed strictly before its shortcut kind is purged"
        )
    }

    func testLogoutClearsCustomTypeCache() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)

        await harness.session.sessionManager.invalidateUserSession(.tokenRejected)

        // Cross-account isolation: a different account signing in on this
        // process must never see the previous user's custom agent cards.
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline, harness.customTypeSync.clearCount < 1 {
            try await Task.sleep(for: .milliseconds(1))
        }
        XCTAssertEqual(harness.customTypeSync.clearCount, 1)
    }

    // MARK: - Account switch

    private static func saveSecondAccount(_ identity: UserIdentityStore) {
        identity.saveUserSession(
            accessToken: "user-token-b",
            email: "b@example.com",
            userId: "u2",
            role: "user"
        )
    }

    private static func token(of connection: FakeWebSocketConnection) -> String? {
        URLComponents(url: connection.url, resolvingAgainstBaseURL: false)?
            .queryItems?.first(where: { $0.name == "token" })?.value
    }

    func testAccountSwitchFromFullyConnectedClearsCachesAndRebindsSocketToNewAccount() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true, agentConnected: true))
        try await assertAccountSwitchRebinds(harness)
    }

    private func assertAccountSwitchRebinds(_ harness: BusHarness) async throws {
        let states = StreamCollector(harness.bus.connectionStates())
        await harness.bus.start()
        let first = try await harness.transportFactory.connection(1)
        first.emit(.opened)
        _ = try await states.next(where: { $0 == .connected })
        XCTAssertEqual(Self.token(of: first), "user-token")

        let identity = harness.session.userIdentityStore
        await harness.session.sessionManager.markUserLoggedIn(userId: "u2") {
            UserEventBusTests.saveSecondAccount(identity)
        }

        let second = try await harness.transportFactory.connection(2)
        XCTAssertEqual(Self.token(of: second), "user-token-b", "the user socket follows the new account")
        XCTAssertEqual(first.closeCalls.count, 1, "the previous account's socket is closed")
        XCTAssertEqual(harness.agentStatus.clearCount, 1)
        XCTAssertEqual(harness.templateSync.clearCount, 1)
        XCTAssertEqual(harness.shortcutSync.clearCount, 1)
        XCTAssertEqual(harness.customTypeSync.clearCount, 1)
        XCTAssertNil(harness.session.tokenStore.getAccessToken(), "the previous account's agent creds are wiped")
    }

    func testSameAccountReLoginKeepsSocketAndCaches() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let states = StreamCollector(harness.bus.connectionStates())
        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        _ = try await states.next(where: { $0 == .connected })

        let identity = harness.session.userIdentityStore
        await harness.session.sessionManager.markUserLoggedIn(userId: "u1") {
            identity.saveUserSession(
                accessToken: "user-token",
                email: "user@example.com",
                userId: "u1",
                role: "user"
            )
        }

        let noNew = await harness.transportFactory.assertNoConnection(beyond: 1)
        XCTAssertTrue(noNew)
        XCTAssertEqual(harness.agentStatus.clearCount, 0)
        XCTAssertEqual(harness.templateSync.clearCount, 0)
        XCTAssertEqual(harness.shortcutSync.clearCount, 0)
        XCTAssertEqual(harness.customTypeSync.clearCount, 0)
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

    // MARK: - Mode-picker order

    func testSessionModeOrderFixtureDemux() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/central/SessionModeOrderDto.json") {
            let envelope = try EnvelopeFixtures.envelope(type: "session_mode_order.updated", payload: fixtureCase.wire)
            guard case .sessionModeOrderUpdated(let order) = UserEnvelopeDemux.demux(envelope) else {
                return XCTFail("case \(fixtureCase.name): expected sessionModeOrderUpdated")
            }
            XCTAssertGreaterThanOrEqual(order.version, 0)
        }
    }

    func testSessionModeOrderPushRoutesToHookAndReconcileRefreshes() async throws {
        let harness = BusHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveAgentUrl("https://agent.example.com")
        let states = StreamCollector(harness.bus.connectionStates())

        await harness.bus.start()
        let connection = try await harness.transportFactory.connection(1)
        connection.emit(.opened)
        _ = try await states.next(where: { $0 == .connected })

        connection.emit(.text(
            #"{"type":"session_mode_order.updated","payload":{"order":["shell","custom_ab12cd34ef56"],"version":3,"updated_at":"2026-11-04T10:00:00Z"}}"#
        ))

        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline,
              harness.customTypeSync.modeOrders.isEmpty || harness.customTypeSync.modeOrderRefreshCount == 0 {
            try await Task.sleep(for: .milliseconds(1))
        }
        XCTAssertEqual(
            harness.customTypeSync.modeOrderRefreshCount, 1,
            "on-connect reconcile re-pulls the mode order, which a push missed while offline would otherwise lose"
        )
        XCTAssertEqual(
            harness.customTypeSync.modeOrders,
            [SessionModeOrderDto(order: ["shell", "custom_ab12cd34ef56"], version: 3, updated_at: "2026-11-04T10:00:00Z")]
        )
    }
}

