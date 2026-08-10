import XCTest
@testable import Mobaide

/// Tests for the reversed (device-code) pairing flow on `AgentListModel`.
///
/// The poll is the only part of this model driven by a timer rather than by a
/// server event, so the behaviour worth pinning is what it does between ticks:
/// survive a dropped request, stop the moment the request settles, and hand off
/// to the shared waiting-for-online tail instead of declaring success itself.
///
/// `StubURLProtocol`'s queue is FIFO across paths, so every test enqueues
/// responses in the exact order the model issues them.
@MainActor
final class AgentListModelDeviceCodeTests: XCTestCase {
    /// Stored (not a per-test local) so `tearDown` can reach it: several tests
    /// below return while the model is still mid-`.confirming` or
    /// mid-`.waitingForHost`, and `deviceCodePollTask` — a plain `Task`, not
    /// bound to the test's lifetime — keeps running past that return. Left
    /// alone it eventually calls `agentRepository.deviceCodeStatus` again
    /// against `StubURLProtocol`'s process-wide static queue, which by then
    /// belongs to the *next* test.
    private var model: AgentListModel!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() async throws {
        // Cancellation is cooperative: a bare `.cancel()` schedules the stop
        // but does not guarantee the task has actually stopped issuing
        // requests by the time this returns. Await each task's own
        // completion so the *next* test's `setUp` is guaranteed to be
        // touching an empty, unclaimed queue.
        //
        // `watcherTask` is the QR flow's "waiting for online" watcher, not
        // device-code work — but `testConfirmHandsOffToTheSharedWaitingForOnlineTail`
        // hands off to it on the success path, so it is exactly as capable
        // of outliving this test and stealing the next test's stub queue.
        let pollTask = model?.deviceCodePollTask
        let mintTask = model?.deviceCodeMintTask
        let watcherTask = model?.watcherTask
        pollTask?.cancel()
        mintTask?.cancel()
        watcherTask?.cancel()
        await pollTask?.value
        await mintTask?.value
        await watcherTask?.value
        model?.resetDeviceCodePairing()
        model?.resetPairing()
        model = nil
        StubURLProtocol.reset()
        try await super.tearDown()
    }

    private static let mintJSON = #"""
    {"request_id":"req-1","code":"WDJB-MJHT","expires_at":"2099-01-01T00:00:00Z",\#
    "verification_command":"mobaide-agent pair WDJB-MJHT"}
    """#

    private func statusJSON(
        _ status: String,
        hostname: String? = "pavel-mbp",
        os: String? = "Darwin",
        isRepair: Bool = false,
        repairAgentName: String? = nil,
        blockedReason: String? = nil
    ) -> String {
        let hostField = hostname.map { "\"\($0)\"" } ?? "null"
        let osField = os.map { "\"\($0)\"" } ?? "null"
        let repairAgentField = repairAgentName.map { "\"\($0)\"" } ?? "null"
        let blockedField = blockedReason.map { "\"\($0)\"" } ?? "null"
        return #"""
        {"status":"\#(status)","expires_at":"2099-01-01T00:00:00Z","hostname":\#(hostField),\#
        "os":\#(osField),"is_repair":\#(isRepair),"repair_agent_name":\#(repairAgentField),\#
        "blocked_reason":\#(blockedField)}
        """#
    }

    private func confirmJSON(id: String = "a1", wsDelivered: Bool = true) -> String {
        #"{"agent_id":"\#(id)","agent_friendly_name":"Workstation","ws_delivered":\#(wsDelivered)}"#
    }

    private func agentsJSON(id: String = "a1", online: Bool) -> String {
        #"[{"agent_id":"\#(id)","name":"Workstation","online":\#(online),"created_at":"t"}]"#
    }

    /// Mint, then tick the poll until it leaves `.waitingForHost`.
    ///
    /// Drives the poll with `sleeper.tick()`, not `release()`: the poll no
    /// longer returns on its own once redeemed — it keeps watching
    /// `.confirming` for a later expiry (see
    /// `testPollKeepsPollingThroughConfirmingUntilTheRequestSettles`) — and
    /// `release()` unblocks every *future* sleep too, so the loop would free
    /// -run past the intended tick(s) and eat whatever is next in
    /// `StubURLProtocol`'s queue before `cancel()` below ever lands.
    ///
    /// Usually one tick suffices, but `testPollSurvivesADroppedRequestAndKeepsWatching`
    /// needs two: a dropped fetch is caught-and-retried without leaving
    /// `.waitingForHost`, so the loop goes straight back to `sleep` for
    /// another tick rather than settling. Looping on that state — not a
    /// fixed tick count — is what makes this correct for both shapes: it
    /// never ticks *more* than the caller's enqueued stubs call for, because
    /// it stops the instant the state actually moves on.
    ///
    /// Each tick is gated on `sleeper.isWaiting` first: `.waitingForHost`
    /// becomes true the moment the poll `Task` is *created*, not once it has
    /// actually run far enough to register its next `sleep`, so a `tick()`
    /// fired right after observing state (or right after the previous tick)
    /// can land on an empty `waiters` and be silently lost.
    private func pollOnce(
        _ model: AgentListModel,
        sleeper: ManualSleeper
    ) async {
        model.startDeviceCodePairing()
        await waitUntil {
            if case .waitingForHost = model.deviceCodePairing { return true }
            return false
        }
        while case .waitingForHost = model.deviceCodePairing {
            await waitUntil { sleeper.isWaiting }
            sleeper.tick()
            await waitUntil {
                if case .waitingForHost = model.deviceCodePairing {
                    return sleeper.isWaiting
                }
                return true
            }
        }
        model.deviceCodePollTask?.cancel()
        await model.deviceCodePollTask?.value
    }

    func testMintShowsTheCommandToRunOnTheAgentHost() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)

        model.startDeviceCodePairing()
        await waitUntil {
            if case .waitingForHost = model.deviceCodePairing { return true }
            return false
        }

        guard case .waitingForHost(let requestId, let code, let command) = model.deviceCodePairing else {
            return XCTFail("expected waitingForHost, got \(model.deviceCodePairing)")
        }
        XCTAssertEqual(requestId, "req-1")
        XCTAssertEqual(code, "WDJB-MJHT")
        // The operator is at a terminal, so the copyable artefact is the whole
        // command; the code alone leaves them guessing the verb.
        XCTAssertEqual(command, "mobaide-agent pair WDJB-MJHT")
        XCTAssertTrue(model.deviceCodeSheetPresented)
    }

    func testPollMovesToConfirmingWhenAMachineEntersTheCode() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(
            status: 200,
            body: statusJSON("redeemed", isRepair: true, repairAgentName: "old-mbp")
        )
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)

        await pollOnce(model, sleeper: sleeper)

        guard case .confirming(_, let hostname, let os, let repairAgentName, let blocked) =
            model.deviceCodePairing else {
            return XCTFail("expected confirming, got \(model.deviceCodePairing)")
        }
        XCTAssertEqual(hostname, "pavel-mbp")
        XCTAssertEqual(os, "Darwin")
        XCTAssertEqual(repairAgentName, "old-mbp")
        XCTAssertNil(blocked)
    }

    func testPollNamesAnUnnamedMachineRatherThanShowingNothing() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(
            status: 200,
            body: statusJSON("redeemed", hostname: nil, os: nil)
        )
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)

        await pollOnce(model, sleeper: sleeper)

        guard case .confirming(_, let hostname, _, _, _) = model.deviceCodePairing else {
            return XCTFail("expected confirming, got \(model.deviceCodePairing)")
        }
        XCTAssertEqual(hostname, "an unnamed machine")
    }

    func testPollSurfacesTheRelaysBlockedVerdictBeforeTheTap() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(
            status: 200,
            body: statusJSON("redeemed", blockedReason: "owned_by_other")
        )
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)

        await pollOnce(model, sleeper: sleeper)

        guard case .confirming(_, _, _, _, let blocked) = model.deviceCodePairing else {
            return XCTFail("expected confirming, got \(model.deviceCodePairing)")
        }
        XCTAssertEqual(blocked, .ownedByOther)
    }

    func testPollTreatsAnUnknownBlockedReasonAsNoWarning() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(
            status: 200,
            body: statusJSON("redeemed", blockedReason: "a_reason_from_a_newer_relay")
        )
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)

        await pollOnce(model, sleeper: sleeper)

        // Losing the warning beats refusing to render the confirm screen; the
        // server still rejects the confirm.
        guard case .confirming(_, _, _, _, let blocked) = model.deviceCodePairing else {
            return XCTFail("expected confirming, got \(model.deviceCodePairing)")
        }
        XCTAssertNil(blocked)
    }

    func testPollReportsACodeThatDiedBeforeAnyMachineUsedIt() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(status: 200, body: statusJSON("expired", hostname: nil, os: nil))
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)

        await pollOnce(model, sleeper: sleeper)

        guard case .error(let message) = model.deviceCodePairing else {
            return XCTFail("expected error, got \(model.deviceCodePairing)")
        }
        XCTAssertTrue(message.contains("expired"))
    }

    func testPollSurvivesADroppedRequestAndKeepsWatching() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(status: 503, body: "gateway blip")
        StubURLProtocol.enqueue(status: 200, body: statusJSON("redeemed"))
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)

        // A dropped poll is not a failed pairing: the code is still live
        // server-side, so the loop keeps going rather than tearing down a
        // request the user can still complete.
        await pollOnce(model, sleeper: sleeper)

        guard case .confirming = model.deviceCodePairing else {
            return XCTFail("expected confirming, got \(model.deviceCodePairing)")
        }
    }

    func testPollKeepsPollingThroughConfirmingUntilTheRequestSettles() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(status: 200, body: statusJSON("redeemed"))
        StubURLProtocol.enqueue(status: 200, body: statusJSON("redeemed"))
        StubURLProtocol.enqueue(status: 200, body: statusJSON("expired"))
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)

        model.startDeviceCodePairing()
        await waitUntil {
            if case .waitingForHost = model.deviceCodePairing { return true }
            return false
        }
        var seen = StubURLProtocol.requests.count // mint

        // Three explicit ticks, one per enqueued status — not `release()`,
        // which would resume every future sleep too and let the loop race
        // ahead of this test's own synchronization. Each tick is gated on
        // `sleeper.isWaiting`: `.waitingForHost`/`.confirming` become true
        // the moment the poll `Task` is *created*, not once it has actually
        // run far enough to register its next `sleep`, so a `tick()` fired
        // right after observing state can land on an empty `waiters` and be
        // silently lost. Between ticks, waiting on the request count alone
        // is not enough either: `StubURLProtocol` records the request on
        // URLSession's loading thread strictly before the response is
        // delivered, which is strictly before the poll's `@MainActor` state
        // update runs — so a count-only wait can resolve a beat before
        // `deviceCodePairing` actually reflects it. Waiting on both closes
        // that gap.
        await waitUntil { sleeper.isWaiting }
        sleeper.tick()
        await waitUntil {
            guard StubURLProtocol.requests.count > seen else { return false }
            if case .confirming = model.deviceCodePairing { return true }
            return false
        }
        guard StubURLProtocol.requests.count > seen, case .confirming = model.deviceCodePairing else {
            return XCTFail(
                "expected confirming after the first redeemed tick, got \(model.deviceCodePairing)"
            )
        }
        seen = StubURLProtocol.requests.count

        await waitUntil { sleeper.isWaiting }
        sleeper.tick()
        await waitUntil {
            guard StubURLProtocol.requests.count > seen else { return false }
            if case .confirming = model.deviceCodePairing { return true }
            return false
        }
        guard StubURLProtocol.requests.count > seen, case .confirming = model.deviceCodePairing else {
            return XCTFail(
                "expected confirming after the second redeemed tick, got \(model.deviceCodePairing)"
            )
        }
        seen = StubURLProtocol.requests.count

        // The nonce can still die while the user is sitting on the confirm
        // screen, so the poll must keep watching for that from `.confirming`
        // too, instead of stopping the moment it first arrives there.
        await waitUntil { sleeper.isWaiting }
        sleeper.tick()
        await waitUntil {
            if case .error = model.deviceCodePairing { return true }
            return false
        }

        guard case .error(let message) = model.deviceCodePairing else {
            return XCTFail("expected error, got \(model.deviceCodePairing)")
        }
        XCTAssertTrue(message.contains("before it was confirmed"), message)
        // mint + 3 polls: both "redeemed" ticks had to land before the
        // "expired" one, proving the loop did not stop at the first redeem.
        XCTAssertEqual(StubURLProtocol.requests.count, seen + 1)
    }

    func testConfirmHandsOffToTheSharedWaitingForOnlineTail() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(status: 200, body: statusJSON("redeemed"))
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)
        await pollOnce(model, sleeper: sleeper)

        // Enqueued only once the poll task is confirmed stopped (`pollOnce`
        // awaits its cancellation), not up front: `waitUntil` polls every
        // 2ms, which is many iterations of an unblocked poll loop — enqueued
        // any earlier, a straggler tick can steal these before `confirmDeviceCode`
        // ever gets to consume them.
        StubURLProtocol.enqueue(status: 200, body: confirmJSON())
        StubURLProtocol.enqueue(status: 200, body: agentsJSON(online: true))
        model.confirmDeviceCode()
        await waitUntil {
            if case .idle = model.deviceCodePairing { return true }
            return false
        }

        // A 200 only proves the relay stored credentials; the agent still has
        // to pick them up and connect back — the QR flow's problem too, which
        // is why the two directions share one watcher. The harness's stubbed
        // agent is already online, so — unlike production, where this can sit
        // in `.waitingForOnline` for real seconds — the watcher settles to
        // `.success` on its own first tick; waiting for that specifically
        // (rather than accepting `.waitingForOnline` too) is what lets
        // `tearDown` find `watcherTask` already finished instead of racing it.
        await waitUntil {
            if case .success = model.qrPairing { return true }
            return false
        }
        XCTAssertTrue(model.pairingSheetPresented)
        XCTAssertFalse(model.deviceCodeSheetPresented)
        guard case .success(let agentId, _) = model.qrPairing else {
            return XCTFail("expected the shared tail to settle, got \(model.qrPairing)")
        }
        XCTAssertEqual(agentId, "a1")
    }

    func testConfirmRefusesAMachineTheRelayAlreadyRejected() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(
            status: 200,
            body: statusJSON("redeemed", blockedReason: "owned_by_other")
        )
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)
        await pollOnce(model, sleeper: sleeper)
        let requestsBefore = StubURLProtocol.requests.count

        model.confirmDeviceCode()
        // Short window: this waits for a request that must never be issued,
        // so the whole timeout is dead time on the happy path.
        await waitUntil(timeout: .milliseconds(300)) {
            StubURLProtocol.requests.count > requestsBefore
        }

        // Confirming would spend both the code and the nonce on a call that
        // cannot succeed.
        XCTAssertEqual(StubURLProtocol.requests.count, requestsBefore)
        guard case .confirming = model.deviceCodePairing else {
            return XCTFail("expected to stay in confirming, got \(model.deviceCodePairing)")
        }
    }

    func testConfirmReadsTheAccountPinRefusalOffTheDiscriminator() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(status: 200, body: statusJSON("redeemed"))
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)
        await pollOnce(model, sleeper: sleeper)

        // See the comment in `testConfirmHandsOffToTheSharedWaitingForOnlineTail`
        // — enqueued only once the poll task is confirmed stopped.
        StubURLProtocol.enqueue(
            status: 409,
            body: #"{"detail":{"error":"trust_key_pin_mismatch"}}"#
        )
        model.confirmDeviceCode()
        await waitUntil {
            if case .error = model.deviceCodePairing { return true }
            return false
        }

        // 409 already means "that machine belongs to another account" here, so
        // only the discriminator tells the two refusals apart.
        guard case .error(let message) = model.deviceCodePairing else {
            return XCTFail("expected error, got \(model.deviceCodePairing)")
        }
        XCTAssertTrue(message.contains("show-trust-fp"), message)
    }

    func testDismissReleasesTheAgentWaitingOnTheNonce() async {
        StubURLProtocol.enqueue(status: 200, body: Self.mintJSON)
        StubURLProtocol.enqueue(status: 200, body: statusJSON("redeemed"))
        let sleeper = ManualSleeper()
        model = AgentHarness().makeAgentListModel(sleeper: sleeper)
        await pollOnce(model, sleeper: sleeper)
        let requestsBefore = StubURLProtocol.requests.count

        // See the comment in `testConfirmHandsOffToTheSharedWaitingForOnlineTail`
        // — enqueued only once the poll task is confirmed stopped.
        StubURLProtocol.enqueue(status: 204, body: "")
        model.dismissDeviceCodePairing()

        // Cancelling expires the nonce server-side, which is what stops the
        // agent's WebSocket hanging for the rest of the window.
        await waitUntil { StubURLProtocol.requests.count > requestsBefore }
        XCTAssertEqual(StubURLProtocol.requests.count, requestsBefore + 1)
        XCTAssertEqual(model.deviceCodePairing, .idle)
        XCTAssertFalse(model.deviceCodeSheetPresented)
    }
}
