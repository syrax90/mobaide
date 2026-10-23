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

    private func isWaitTimedOut(_ state: QrPairingState) -> Bool {
        if case .waitTimedOut = state { return true }
        return false
    }

    /// `GET /api/agents` calls recorded so far: the claim's own refresh, the
    /// watcher's pre-loop refresh and every defensive poll.
    private var agentListFetches: Int {
        StubURLProtocol.requests.filter { $0.httpMethod == "GET" && $0.url?.path == "/api/agents" }.count
    }

    /// Advance the parked watcher by `count` 1 s ticks. Each waits for the
    /// loop to reach its next `sleep` first — a `tick()` fired before the
    /// loop parks resumes nothing and is silently lost.
    private func tick(_ sleeper: ManualSleeper, times count: Int) async {
        for _ in 0..<count {
            await waitUntil { sleeper.isWaiting }
            sleeper.tick()
        }
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

    func testTimeoutGoesToWaitTimedOutNotError() async {
        // The claim landed and the agent stays paired; running out the budget
        // is "not online yet", never `.error`. The 5 s polls read the empty
        // stub (`{}`), fail to decode and are swallowed — the agent stays offline.
        let sleeper = ManualSleeper()
        let model = AgentHarness().makeAgentListModel(sleeper: sleeper, now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 200, body: claimResponse(id: "a1", wsDelivered: false))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))  // claim refresh
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))  // pre-loop refresh
        model.confirmClaim()
        await waitUntil { sleeper.durations.count >= 1 }
        sleeper.release()  // all 60 ticks run; never online
        await waitUntil { self.isWaitTimedOut(model.qrPairing) || self.isError(model.qrPairing) }
        XCTAssertEqual(
            model.qrPairing,
            .waitTimedOut(agentId: "a1", friendlyName: "Agent a1", wsDeliveredAtClaim: false)
        )
    }

    func testWaitTimeoutMessageIsPlatformNeutral() {
        let staged = AgentListModel.waitTimeoutMessage(friendlyName: "lab-host", wsDeliveredAtClaim: false)
        let delivered = AgentListModel.waitTimeoutMessage(friendlyName: "lab-host", wsDeliveredAtClaim: true)
        let unknown = AgentListModel.waitTimeoutMessage(friendlyName: "lab-host", wsDeliveredAtClaim: nil)

        XCTAssertTrue(staged.contains("‘lab-host’ was paired to your account"), staged)
        XCTAssertTrue(staged.contains("60 s"), staged)
        XCTAssertTrue(staged.contains("`mobaide-agent show-qr`"), staged)
        XCTAssertFalse(staged.contains("did not come online"), staged)
        XCTAssertFalse(staged.contains("Get-ScheduledTask"), staged)
        // Tri-state hint: `nil` (older relay) claims nothing about the push.
        XCTAssertTrue(staged.contains("staged"), staged)
        XCTAssertFalse(staged.contains("tunnel"), staged)
        XCTAssertTrue(delivered.contains("tunnel"), delivered)
        XCTAssertFalse(delivered.contains("staged"), delivered)
        XCTAssertFalse(unknown.contains("tunnel"), unknown)
        XCTAssertFalse(unknown.contains("staged"), unknown)
    }

    func testDefensivePollRecoversWhenPushNeverArrives() async {
        let sleeper = ManualSleeper()
        let model = AgentHarness().makeAgentListModel(sleeper: sleeper, now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 200, body: claimResponse(id: "a1", wsDelivered: true))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))  // claim refresh
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))  // pre-loop refresh
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))   // 5 s poll
        model.confirmClaim()

        await tick(sleeper, times: 4)
        await waitUntil { sleeper.isWaiting }  // tick 4 judged offline and parked again
        XCTAssertEqual(agentListFetches, 2, "no poll before the 5 s mark")
        guard case .waitingForOnline(_, _, let elapsed, _) = model.qrPairing else {
            return XCTFail("expected waitingForOnline, got \(model.qrPairing)")
        }
        XCTAssertEqual(elapsed, 4)

        await tick(sleeper, times: 1)
        await waitUntil { self.isSuccess(model.qrPairing) }

        XCTAssertEqual(model.qrPairing, .success(agentId: "a1", friendlyName: "Agent a1"))
        XCTAssertEqual(agentListFetches, 3, "exactly one poll, at the 5 s mark")
    }

    func testFinalRefetchAtDeadlineWins() async {
        let sleeper = ManualSleeper()
        let model = AgentHarness().makeAgentListModel(sleeper: sleeper, now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 200, body: claimResponse(id: "a1", wsDelivered: false))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))  // claim refresh
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))  // pre-loop refresh
        for _ in 0..<11 {  // polls at 5 s … 55 s
            StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))
        }
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))  // the 60 s deadline poll
        model.confirmClaim()
        await waitUntil { sleeper.durations.count >= 1 }
        sleeper.release()
        await waitUntil { self.isSuccess(model.qrPairing) || self.isWaitTimedOut(model.qrPairing) }

        XCTAssertEqual(model.qrPairing, .success(agentId: "a1", friendlyName: "Agent a1"))
        XCTAssertEqual(agentListFetches, 14)
    }

    func testKeepWaitingRestartsWatcher() async {
        let sleeper = ManualSleeper()
        let model = AgentHarness().makeAgentListModel(sleeper: sleeper, now: { Self.fixedNow })
        model.onScanResult(payload(exp: 5000))
        StubURLProtocol.enqueue(status: 200, body: claimResponse(id: "a1", wsDelivered: false))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: false))
        model.confirmClaim()
        await waitUntil { sleeper.durations.count >= 1 }
        sleeper.release()
        await waitUntil { self.isWaitTimedOut(model.qrPairing) }
        let fetchesBeforeRestart = agentListFetches

        // The released sleeper resolves every later sleep instantly, so the
        // restarted loop reaches its first check as soon as its own pre-loop
        // refresh — which now reads the agent online — lands.
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(id: "a1", online: true))
        model.keepWaiting()

        XCTAssertEqual(
            model.qrPairing,
            .waitingForOnline(agentId: "a1", friendlyName: "Agent a1", elapsedSeconds: 0, wsDeliveredAtClaim: false)
        )
        XCTAssertNotNil(model.watcherTask, "keepWaiting re-arms the watcher")
        await waitUntil { self.isSuccess(model.qrPairing) }
        XCTAssertEqual(model.qrPairing, .success(agentId: "a1", friendlyName: "Agent a1"))
        XCTAssertEqual(agentListFetches, fetchesBeforeRestart + 1, "one fresh pre-loop refresh")
    }

    func testKeepWaitingOutsideTimedOutIsNoOp() {
        let model = AgentHarness().makeAgentListModel(now: { Self.fixedNow })

        model.keepWaiting()

        XCTAssertEqual(model.qrPairing, .idle)
        XCTAssertNil(model.watcherTask)
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
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

    func testConfirmDeleteWithUninstallSendsQueryAndAnnouncesIt() async {
        let harness = AgentHarness()
        let model = harness.makeAgentListModel()

        model.startDelete(agent(id: "a2", name: "Workstation", online: true))
        StubURLProtocol.enqueue(status: 204, body: "")
        StubURLProtocol.enqueue(status: 200, body: "[]")
        model.confirmDelete(uninstall: true)
        await waitUntil { model.message != nil }

        let delete = StubURLProtocol.requests.first { $0.httpMethod == "DELETE" }
        XCTAssertEqual(delete?.url?.path, "/api/agents/a2")
        XCTAssertEqual(delete?.url?.query, "uninstall=true")
        XCTAssertEqual(model.message, "“Workstation” is uninstalling itself.")
    }

    func testConfirmDeleteWithUninstallOfOfflineAgentAnnouncesTheDeferral() async {
        let harness = AgentHarness()
        let model = harness.makeAgentListModel()

        model.startDelete(agent(id: "a2", name: "Workstation", online: false))
        StubURLProtocol.enqueue(status: 204, body: "")
        StubURLProtocol.enqueue(status: 200, body: "[]")
        model.confirmDelete(uninstall: true)
        await waitUntil { model.message != nil }

        XCTAssertEqual(
            model.message,
            "“Workstation” will uninstall itself when it next comes online."
        )
    }

    func testConfirmDeleteWithoutUninstallSendsNoQueryAndNoMessage() async {
        let harness = AgentHarness()
        let model = harness.makeAgentListModel()

        model.startDelete(agent(id: "a2", name: "Workstation", online: true))
        StubURLProtocol.enqueue(status: 204, body: "")
        StubURLProtocol.enqueue(status: 200, body: "[]")
        model.confirmDelete()
        await waitUntil { StubURLProtocol.requests.count >= 2 }

        let delete = StubURLProtocol.requests.first { $0.httpMethod == "DELETE" }
        XCTAssertEqual(delete?.url?.path, "/api/agents/a2")
        XCTAssertNil(delete?.url?.query)
        XCTAssertNil(model.message)
    }
}
