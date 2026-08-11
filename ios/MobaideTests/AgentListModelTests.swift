import XCTest
@testable import Mobaide

@MainActor
final class AgentListModelTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private nonisolated static let fixedNow = Date(timeIntervalSince1970: 1000)

    private func payload(exp: Int64, relay: String = "https://relay.example.com", fp: String = "fp-1", nonce: String = "nonce-1") -> String {
        "MOBA1|\(relay)|\(nonce)|\(fp)|\(exp)"
    }

    private func claimResponse(id: String, wsDelivered: Bool) -> String {
        #"{"agent_id":"\#(id)","agent_friendly_name":"Agent \#(id)","ws_delivered":\#(wsDelivered)}"#
    }

    private func agentsJSON(id: String, online: Bool) -> String {
        #"[{"agent_id":"\#(id)","name":"Agent \#(id)","online":\#(online),"created_at":"t"}]"#
    }

    private func isSuccess(_ state: QrPairingState) -> Bool {
        if case .success = state { return true }
        return false
    }

    private func isError(_ state: QrPairingState) -> Bool {
        if case .error = state { return true }
        return false
    }

    // MARK: - Scan → expiry → TOFU

    func testScanValidGoesToConfirming() {
        let model = AgentHarness().makeAgentListModel(now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        guard case .confirming(let parsed) = model.qrPairing else {
            return XCTFail("expected confirming, got \(model.qrPairing)")
        }
        XCTAssertEqual(parsed.nonce, "nonce-1")
    }

    func testScanExpiredGoesToError() {
        let model = AgentHarness().makeAgentListModel(now: { Self.fixedNow })
        model.onScanResult(payload(exp: 1000))  // exp == now → expired (<=)
        guard case .error(let message) = model.qrPairing else {
            return XCTFail("expected error")
        }
        XCTAssertTrue(message.contains("expired"))
    }

    func testScanMalformedGoesToError() {
        let model = AgentHarness().makeAgentListModel(now: { Self.fixedNow })
        model.onScanResult("not-a-pairing-code")
        XCTAssertTrue(isError(model.qrPairing))
    }

    func testScanTofuMismatchGoesToError() {
        let harness = AgentHarness()
        harness.trustKeyPinning.setPinned(relayUrl: "https://relay.example.com", fingerprint: "fp-OLD")
        let model = harness.makeAgentListModel(now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000, fp: "fp-NEW"))
        guard case .error(let message) = model.qrPairing else {
            return XCTFail("expected error")
        }
        XCTAssertTrue(message.contains("Trust key changed"))
    }

    // MARK: - Claim

    func testClaim410GoesToError() async {
        let model = AgentHarness().makeAgentListModel(now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 410, body: "gone")
        model.confirmClaim()
        await waitUntil { self.isError(model.qrPairing) }
        guard case .error(let message) = model.qrPairing else {
            return XCTFail("expected error")
        }
        XCTAssertTrue(message.contains("consumed or expired"))
    }

    // MARK: - Waiting-for-online watcher

    func testSeedOnlineSucceedsImmediately() async {
        let harness = AgentHarness()
        let model = harness.makeAgentListModel(now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 200, body: claimResponse(id: "a1", wsDelivered: true))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))  // claim refresh → online
        model.confirmClaim()
        await waitUntil { self.isSuccess(model.qrPairing) }
        guard case .success(let id, _) = model.qrPairing else {
            return XCTFail("expected success")
        }
        XCTAssertEqual(id, "a1")
        XCTAssertNil(model.watcherTask)  // no watcher launched
    }

    func testRestBeatsWs() async {
        let sleeper = ManualSleeper()
        let model = AgentHarness().makeAgentListModel(sleeper: sleeper, now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 200, body: claimResponse(id: "a1", wsDelivered: false))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))  // claim refresh → offline (seed fails)
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))   // watcher pre-loop refresh → online
        model.confirmClaim()
        await waitUntil { sleeper.durations.count >= 1 }  // watcher reached first tick (pre-loop refresh done)
        sleeper.release()
        await waitUntil { self.isSuccess(model.qrPairing) }
        XCTAssertTrue(isSuccess(model.qrPairing))
    }

    func testWsBeatsRest() async {
        let sleeper = ManualSleeper()
        let harness = AgentHarness()
        let model = harness.makeAgentListModel(sleeper: sleeper, now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 200, body: claimResponse(id: "a1", wsDelivered: true))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))  // claim refresh → offline
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))  // watcher pre-loop refresh → offline
        model.confirmClaim()
        await waitUntil { sleeper.durations.count >= 1 }
        await harness.agentRepository.updateAgentOnline(agentId: "a1", online: true)  // WS push wins
        sleeper.release()
        await waitUntil { self.isSuccess(model.qrPairing) }
        XCTAssertTrue(isSuccess(model.qrPairing))
    }

    func testTimeoutGoesToError() async {
        let sleeper = ManualSleeper()
        let model = AgentHarness().makeAgentListModel(sleeper: sleeper, now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 200, body: claimResponse(id: "a1", wsDelivered: false))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))
        model.confirmClaim()
        await waitUntil { sleeper.durations.count >= 1 }
        sleeper.release()  // all 60 ticks run; never online
        await waitUntil { self.isError(model.qrPairing) }
        guard case .error(let message) = model.qrPairing else {
            return XCTFail("expected error")
        }
        XCTAssertTrue(message.contains("did not come online"))
    }

    func testWatcherCancelsCleanlyOnDismiss() async {
        let sleeper = ManualSleeper()
        let model = AgentHarness().makeAgentListModel(sleeper: sleeper, now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 200, body: claimResponse(id: "a1", wsDelivered: false))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))
        model.confirmClaim()
        await waitUntil { sleeper.durations.count >= 1 }  // watcher blocked on first tick

        let task = model.watcherTask
        model.dismissPairing()  // cancels the watcher
        await task?.value       // deterministically wait for it to unwind

        XCTAssertEqual(model.qrPairing, .idle)
        XCTAssertTrue(model.watcherCancellationObserved)
    }

    // MARK: - Helpers for connect/add/re-register/delete

    private func agent(id: String, name: String = "Agent", online: Bool = false) -> AgentSummaryResponse {
        AgentSummaryResponse(agent_id: id, name: name, online: online, created_at: "t", last_seen_at: nil)
    }

    private func mobileTokensJSON(access: String = "relay-at", refresh: String = "relay-rt") -> String {
        #"{"access_token":"\#(access)","refresh_token":"\#(refresh)","token_type":"bearer"}"#
    }

    // MARK: - Connect (relay)

    func testConnectPersistsRelayCredentialsAndNavigates() async {
        let harness = AgentHarness()
        let model = harness.makeAgentListModel()
        StubURLProtocol.enqueue(status: 200, body: mobileTokensJSON())

        model.connect(agent(id: "a1", name: "Prod Box"))
        await waitUntil { model.connectedAgentId == "a1" }

        XCTAssertEqual(model.connectedAgentId, "a1")
        XCTAssertFalse(model.isConnecting)
        XCTAssertEqual(harness.router.path, [.sessionList])
        XCTAssertEqual(harness.session.tokenStore.getConnectionMode(), .relay)
        XCTAssertEqual(harness.session.tokenStore.getAgentId(), "a1")
        XCTAssertEqual(harness.session.tokenStore.getAccessToken(), "relay-at")

        let profiles = harness.connectionStore.listProfiles()
        XCTAssertEqual(profiles.count, 1)
        XCTAssertEqual(profiles.first?.mode, "relay")
        XCTAssertEqual(profiles.first?.agentId, "a1")
        XCTAssertEqual(profiles.first?.label, "Agent: Prod Box")
    }

    func testConnectAlreadyConnectedNavigatesWithoutNetwork() {
        let harness = AgentHarness()
        harness.session.tokenStore.saveRelayCredentials(
            serverUrl: "https://central.example.com", agentId: "a1", pairingId: "a1",
            deviceId: "d", accessToken: "at", refreshToken: "rt"
        )
        let model = harness.makeAgentListModel()
        XCTAssertEqual(model.connectedAgentId, "a1")

        model.connect(agent(id: "a1"))

        XCTAssertEqual(harness.router.path, [.sessionList])
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "already-connected shortcut makes no network call")
    }

    func testConnectStaleSnapshotDoesNotShortCircuitAfterASwitchBehindTheModel() async {
        let harness = AgentHarness()
        harness.session.tokenStore.saveRelayCredentials(
            serverUrl: "https://central.example.com", agentId: "a1", pairingId: "a1",
            deviceId: "d", accessToken: "at", refreshToken: "rt"
        )
        let model = harness.makeAgentListModel()
        XCTAssertEqual(model.connectedAgentId, "a1")

        // A tapped notification switched agents behind this model.
        harness.session.tokenStore.saveRelayCredentials(
            serverUrl: "https://central.example.com", agentId: "a2", pairingId: "a2",
            deviceId: "d", accessToken: "at2", refreshToken: "rt2"
        )
        StubURLProtocol.enqueue(status: 200, body: mobileTokensJSON())

        model.connect(agent(id: "a1", name: "Prod Box"))
        await waitUntil { harness.session.tokenStore.getAgentId() == "a1" }

        XCTAssertEqual(
            StubURLProtocol.requests.count, 1,
            "a stale snapshot must not skip the connect and land on another agent's session list"
        )
        XCTAssertEqual(harness.session.tokenStore.getAgentId(), "a1")
        XCTAssertEqual(model.connectedAgentId, "a1")
        XCTAssertEqual(harness.router.path, [.sessionList])
    }

    func testConnectShortCircuitsForAnAgentConnectedBehindTheModel() async {
        let harness = AgentHarness()
        let model = harness.makeAgentListModel()
        XCTAssertNil(model.connectedAgentId)

        harness.session.tokenStore.saveRelayCredentials(
            serverUrl: "https://central.example.com", agentId: "a2", pairingId: "a2",
            deviceId: "d", accessToken: "at", refreshToken: "rt"
        )
        model.connect(agent(id: "a2"))

        XCTAssertFalse(model.isConnecting, "the agent already connected must not be torn down and reconnected")
        XCTAssertEqual(model.connectedAgentId, "a2")
        XCTAssertEqual(harness.router.path, [.sessionList])
        await waitUntil(timeout: .milliseconds(200)) { false }
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testConnectFailureSurfacesMessageAndDoesNotNavigate() async {
        let harness = AgentHarness()
        let model = harness.makeAgentListModel()
        StubURLProtocol.enqueue(status: 500, body: "boom")

        model.connect(agent(id: "a1"))
        await waitUntil { model.message != nil }

        XCTAssertTrue(model.message?.contains("Failed to connect") == true)
        XCTAssertNil(model.connectedAgentId)
        XCTAssertTrue(harness.router.path.isEmpty)
        XCTAssertFalse(model.isConnecting)
    }

    // MARK: - Add agent

    func testSubmitAddShowsOneTimeCredentials() async {
        let model = AgentHarness().makeAgentListModel()
        model.startAdd()
        model.addName = "  My Agent  "
        StubURLProtocol.enqueue(status: 200, body: #"{"agent_id":"a9","name":"My Agent","token":"tok-9"}"#)
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a9", online: false))

        model.submitAdd()
        await waitUntil { model.credentials != nil }

        XCTAssertEqual(model.credentials?.agentId, "a9")
        XCTAssertEqual(model.credentials?.token, "tok-9")
        XCTAssertEqual(model.credentials?.title, "Agent Created")
        XCTAssertFalse(model.showAddInput)
        XCTAssertFalse(model.isAddBusy)
    }

    func testSubmitAddBlankNameIsNoOp() {
        let model = AgentHarness().makeAgentListModel()
        model.startAdd()
        model.addName = "   "

        model.submitAdd()

        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
        XCTAssertNil(model.credentials)
        XCTAssertTrue(model.showAddInput, "blank name keeps the add dialog open")
        XCTAssertFalse(model.isAddBusy)
    }

    // MARK: - Re-register

    func testConfirmReRegisterShowsNewCredentialsWithoutRefresh() async {
        let model = AgentHarness().makeAgentListModel()
        model.startReRegister(agent(id: "a1", name: "Box"))
        StubURLProtocol.enqueue(status: 200, body: #"{"agent_id":"a1","token":"rotated"}"#)

        model.confirmReRegister()
        await waitUntil { model.credentials != nil }

        XCTAssertEqual(model.credentials?.title, "New Credentials")
        XCTAssertEqual(model.credentials?.token, "rotated")
        XCTAssertEqual(model.credentials?.agentId, "a1")
        XCTAssertNil(model.reregisterConfirm)
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "re-register rotates creds only; no list refresh")
        XCTAssertFalse(model.isReRegisterBusy)
    }

    // MARK: - Delete

    func testConfirmDeleteConnectedAgentDisconnectsFirst() async {
        let harness = AgentHarness()
        harness.session.tokenStore.saveRelayCredentials(
            serverUrl: "https://central.example.com", agentId: "a1", pairingId: "a1",
            deviceId: "d", accessToken: "at", refreshToken: "rt"
        )
        await harness.session.sessionManager.markAgentConnected()
        let model = harness.makeAgentListModel()
        XCTAssertEqual(model.connectedAgentId, "a1")

        model.startDelete(agent(id: "a1"))
        StubURLProtocol.enqueue(status: 204, body: "")
        StubURLProtocol.enqueue(status: 200, body: "[]")
        model.confirmDelete()
        await waitUntil { StubURLProtocol.requests.count >= 2 }

        XCTAssertNil(model.connectedAgentId, "connected agent cleared on delete")
        let state = await harness.session.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly, "delete of the connected agent disconnects the session first")
        XCTAssertNil(model.deleteConfirm)
    }

    func testConfirmDeleteOtherAgentKeepsConnection() async {
        let harness = AgentHarness()
        harness.session.tokenStore.saveRelayCredentials(
            serverUrl: "https://central.example.com", agentId: "a1", pairingId: "a1",
            deviceId: "d", accessToken: "at", refreshToken: "rt"
        )
        await harness.session.sessionManager.markAgentConnected()
        let model = harness.makeAgentListModel()

        model.startDelete(agent(id: "a2"))
        StubURLProtocol.enqueue(status: 204, body: "")
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))
        model.confirmDelete()
        await waitUntil { StubURLProtocol.requests.count >= 2 }

        XCTAssertEqual(model.connectedAgentId, "a1", "deleting a different agent leaves the connection intact")
        let state = await harness.session.sessionManager.currentAuthState
        XCTAssertEqual(state, .fullyConnected)
    }
}
