import Foundation
import Observation

/// One-time agent credentials shown after add / re-register (Android
/// `AgentCredentialsSheet` inputs). The token is displayed once.
struct AgentCredentials: Identifiable, Equatable {
    let agentId: String
    let token: String
    let title: String
    var id: String { agentId + "|" + title }
}

/// Agents list + QR-pairing + connect/add/re-register/delete state (Android
/// `AgentListViewModel`). The list is push-driven via `AgentRepository`
/// (REST refreshes + `agent.status_changed`); pairing runs the
/// `QrPairingState` machine and a ≤60 s watcher polling the same cache, so
/// whichever of REST/WS flips the new agent online first wins.
@MainActor
@Observable
final class AgentListModel {
    static let waitForOnlineTimeoutSeconds = 60
    static let deviceCodePollIntervalSeconds = 2
    /// The `detail.error` discriminator on the relay's account-trust-pin refusal.
    static let trustPinMismatchCode = "trust_key_pin_mismatch"

    private(set) var agents: [AgentSummaryResponse] = []
    private(set) var connectedAgentId: String?
    private(set) var isConnecting = false

    var qrPairing: QrPairingState = .idle
    /// Drives the single pairing sheet whose content swaps scanner → flow as
    /// `qrPairing` advances (avoids a dismiss-then-present sheet glitch).
    var pairingSheetPresented = false

    var deviceCodePairing: DeviceCodePairingState = .idle
    /// Separate presentation flag from `pairingSheetPresented`: the two flows
    /// can never be active at once, but the device-code sheet hands off to the
    /// QR sheet for the shared waiting-for-online tail, and one flag cannot
    /// express that swap.
    var deviceCodeSheetPresented = false
    var deviceCodePollTask: Task<Void, Never>?
    var deviceCodeMintTask: Task<Void, Never>?

    var showAddInput = false
    var addName = ""
    private(set) var isAddBusy = false

    var reregisterConfirm: AgentSummaryResponse?
    private(set) var isReRegisterBusy = false

    var deleteConfirm: AgentSummaryResponse?
    var credentials: AgentCredentials?
    var showInstallSheet = false

    var message: String?

    /// Set when the watcher unwinds via cancellation — asserted by tests to
    /// prove clean teardown (no leaked task/continuation).
    private(set) var watcherCancellationObserved = false
    var watcherTask: Task<Void, Never>?

    private let agentRepository: AgentRepository
    private let authRepository: AuthRepository
    private let trustKeyPinning: TrustKeyPinning
    private let deviceIdentityStore: DeviceIdentityStore
    private let router: Router
    private let sleeper: any Sleeper
    private let now: @Sendable () -> Date

    init(
        agentRepository: AgentRepository,
        authRepository: AuthRepository,
        trustKeyPinning: TrustKeyPinning,
        deviceIdentityStore: DeviceIdentityStore,
        tokenStore: TokenStore,
        router: Router,
        sleeper: any Sleeper = ContinuousSleeper(),
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.agentRepository = agentRepository
        self.authRepository = authRepository
        self.trustKeyPinning = trustKeyPinning
        self.deviceIdentityStore = deviceIdentityStore
        self.router = router
        self.sleeper = sleeper
        self.now = now
        self.connectedAgentId = tokenStore.getAgentId()
        self.agents = agentRepository.currentAgents
    }

    isolated deinit {
        watcherTask?.cancel()
        deviceCodePollTask?.cancel()
        deviceCodeMintTask?.cancel()
    }

    var isEmpty: Bool { agents.isEmpty }

    func observeAgents() async {
        for await list in agentRepository.agents() {
            agents = list
        }
    }

    func refresh() async {
        do {
            try await agentRepository.refreshAgents()
        } catch {
            message = "Failed to load agents: \(ErrorText.message(for: error))"
        }
    }

    func clearMessage() { message = nil }

    // MARK: - Connect (tap an agent card)

    func connect(_ agent: AgentSummaryResponse) {
        guard !isConnecting else { return }
        if agent.agent_id == connectedAgentId {
            router.navigate(to: .sessionList)
            return
        }
        isConnecting = true
        Task { [weak self] in
            guard let self else { return }
            defer { self.isConnecting = false }
            do {
                try await self.authRepository.connectAgentViaRelay(
                    agentId: agent.agent_id,
                    friendlyName: agent.name,
                    deviceId: self.deviceIdentityStore.getOrCreateDeviceId(),
                    deviceName: self.deviceIdentityStore.getDeviceName()
                )
                self.connectedAgentId = agent.agent_id
                self.router.navigate(to: .sessionList)
            } catch {
                self.message = "Failed to connect: \(ErrorText.message(for: error))"
            }
        }
    }

    // MARK: - Add agent (manual, secondary)

    func startAdd() {
        addName = ""
        showAddInput = true
    }

    func submitAdd() {
        let name = addName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        showAddInput = false
        isAddBusy = true
        Task { [weak self] in
            guard let self else { return }
            defer { self.isAddBusy = false }
            do {
                let created = try await self.agentRepository.createAgent(name: name)
                self.credentials = AgentCredentials(
                    agentId: created.agent_id,
                    token: created.token,
                    title: "Agent Created"
                )
            } catch {
                self.message = "Failed to create agent: \(ErrorText.message(for: error))"
            }
        }
    }

    // MARK: - Re-register

    func startReRegister(_ agent: AgentSummaryResponse) {
        reregisterConfirm = agent
    }

    func confirmReRegister() {
        guard let agent = reregisterConfirm else { return }
        reregisterConfirm = nil
        isReRegisterBusy = true
        Task { [weak self] in
            guard let self else { return }
            defer { self.isReRegisterBusy = false }
            do {
                let response = try await self.agentRepository.reregisterAgent(agentId: agent.agent_id)
                self.credentials = AgentCredentials(
                    agentId: response.agent_id,
                    token: response.token,
                    title: "New Credentials"
                )
            } catch {
                self.message = "Failed to re-register agent: \(ErrorText.message(for: error))"
            }
        }
    }

    // MARK: - Delete

    func startDelete(_ agent: AgentSummaryResponse) {
        deleteConfirm = agent
    }

    func confirmDelete() {
        guard let agent = deleteConfirm else { return }
        deleteConfirm = nil
        Task { [weak self] in
            guard let self else { return }
            do {
                if agent.agent_id == self.connectedAgentId {
                    await self.authRepository.disconnectAgent()
                    self.connectedAgentId = nil
                }
                try await self.agentRepository.deleteAgent(agentId: agent.agent_id)
                // Drop any saved relay profile for the now-deleted agent.
                self.authRepository.removeRelayProfile(agentId: agent.agent_id)
            } catch {
                self.message = "Failed to delete agent: \(ErrorText.message(for: error))"
            }
        }
    }

    // MARK: - QR pairing state machine

    func startScan() {
        resetPairing()
        pairingSheetPresented = true
    }

    /// Scanner delivered a raw QR string. Parse → expiry → TOFU → confirm.
    /// The pairing sheet stays open; only its content swaps.
    func onScanResult(_ raw: String) {
        guard let payload = PairingPayload.parseMoba1(raw) else {
            qrPairing = .error("Scanned QR is not a Mobaide pairing code.")
            return
        }
        if payload.expUnix <= Int64(now().timeIntervalSince1970) {
            qrPairing = .error("QR expired. Render a fresh one on the agent.")
            return
        }
        switch trustKeyPinning.verifyOrPin(relayUrl: payload.relayUrl, fingerprint: payload.trustKeyFp) {
        case .mismatch:
            qrPairing = .error(
                "Trust key changed for \(payload.relayUrl) — possible MITM. Refusing to claim. "
                    + "If you intentionally rotated the relay key, clear app data to re-pin."
            )
        case .match, .pinned:
            qrPairing = .confirming(payload)
        }
    }

    func onScanCancelled() {
        dismissPairing()
    }

    func confirmClaim() {
        guard case .confirming(let payload) = qrPairing else { return }
        qrPairing = .claiming(payload)
        Task { [weak self] in
            guard let self else { return }
            let request = PairingClaimRequest(
                nonce: payload.nonce,
                trust_key_fp: payload.trustKeyFp,
                mobile_device_id: self.deviceIdentityStore.getOrCreateDeviceId(),
                mobile_device_name: self.deviceIdentityStore.getDeviceName(),
                platform: "ios"
            )
            do {
                let response = try await self.agentRepository.claimAgentByPairing(request: request)
                self.startWaitingForOnlineWatcher(
                    agentId: response.agent_id,
                    friendlyName: response.agent_friendly_name,
                    wsDeliveredAtClaim: response.ws_delivered
                )
            } catch let error as APIError where error.code == 410 {
                self.qrPairing = .error("Pairing code consumed or expired. Render a fresh QR on the agent.")
            } catch {
                self.qrPairing = .error("Pairing failed: \(ErrorText.message(for: error))")
            }
        }
    }

    // MARK: - Device-code pairing (browser/phone mints, operator retypes)

    /// Mint a pairing code and start watching for a machine to enter it.
    func startDeviceCodePairing() {
        guard case .idle = deviceCodePairing else { return }
        deviceCodePairing = .minting
        deviceCodeSheetPresented = true
        // Retained and re-checked after the await: the minting sheet is
        // interactively dismissible, so a swipe-down mid-mint would otherwise
        // leave this task writing `.waitingForHost` over an `.idle` state and
        // starting a poll with no UI.
        deviceCodeMintTask = Task { [weak self] in
            guard let self else { return }
            do {
                let minted = try await self.agentRepository.mintDeviceCode()
                guard case .minting = self.deviceCodePairing else {
                    // The user backed out while the mint was in flight; release
                    // the code instead of letting it age out.
                    try? await self.agentRepository.cancelDeviceCode(
                        requestId: minted.request_id
                    )
                    return
                }
                self.deviceCodePairing = .waitingForHost(
                    requestId: minted.request_id,
                    code: minted.code,
                    command: minted.verification_command
                )
                self.startDeviceCodePoll(
                    requestId: minted.request_id,
                    expiresAt: minted.expires_at
                )
            } catch {
                guard case .minting = self.deviceCodePairing else { return }
                self.deviceCodePairing = .error(Self.deviceCodeMessage(for: error))
            }
        }
    }

    /// Poll until a machine redeems the code, or the code dies.
    ///
    /// Polled rather than pushed, unlike everything else this model reads:
    /// a redemption is a step inside a handshake, not a change to any entity a
    /// `*.changed` event describes. Runs until the request reaches a terminal
    /// status or its deadline passes — deliberately *through* `.confirming`,
    /// since the confirm window is the nonce's and an expiry that arrives
    /// while the user is deciding has to reach the screen.
    private func startDeviceCodePoll(requestId: String, expiresAt: String) {
        deviceCodePollTask?.cancel()
        // Bounded by the request's own deadline rather than `while true`: a
        // client that loses connectivity with the sheet open would otherwise
        // poll every two seconds for the life of the process.
        // `CountdownView.parse` is the codebase's one reader of relay ISO
        // timestamps (it handles both the fractional and plain forms Python's
        // `isoformat()` can emit). A shape it cannot read yields nil, which
        // leaves the loop unbounded — the same behaviour as before this bound
        // existed, so an unparseable deadline degrades rather than breaks.
        // Re-read on every poll because the deadline *moves*: before
        // redemption it is the code's expiry, and once a machine redeems the
        // relay swaps in the nonce's — a different, later clock. Pinning the
        // mint value would declare a still-confirmable request expired.
        var deadline = CountdownView.parse(expiresAt)
        deviceCodePollTask = Task { [weak self] in
            guard let self else { return }
            do {
                while deadline.map({ self.now() < $0 }) ?? true {
                    try Task.checkCancellation()
                    try await self.sleeper.sleep(for: .seconds(Self.deviceCodePollIntervalSeconds))
                    // Polling continues through `.confirming`, deliberately:
                    // the confirm window is the nonce's, so a user who sits on
                    // the screen past it would otherwise be looking at a live
                    // button whose only possible outcome is a 410.
                    guard self.isDeviceCodePollable else { return }
                    let status: DeviceCodeStatusResponse
                    do {
                        status = try await self.agentRepository.deviceCodeStatus(requestId: requestId)
                    } catch is CancellationError {
                        throw CancellationError()
                    } catch {
                        // A dropped poll is not a failed pairing: the code is
                        // still live server-side, so keep polling rather than
                        // tearing down a request the user can still complete.
                        continue
                    }
                    guard self.isDeviceCodePollable else { return }
                    if let moved = CountdownView.parse(status.expires_at) {
                        deadline = moved
                    }
                    switch status.status {
                    case "redeemed":
                        self.deviceCodePairing = .confirming(
                            requestId: requestId,
                            hostname: status.hostname ?? "an unnamed machine",
                            os: status.os,
                            repairAgentName: status.repair_agent_name,
                            blockedReason: DeviceCodeBlockedReason(wire: status.blocked_reason)
                        )
                    case "expired", "canceled":
                        self.deviceCodePairing = .error(self.deviceCodeExpiredMessage)
                        return
                    default:
                        continue
                    }
                }
                if self.isDeviceCodePollable {
                    self.deviceCodePairing = .error(self.deviceCodeExpiredMessage)
                }
            } catch {
                // Cancellation (dismiss / deinit) unwinds here.
                self.watcherCancellationObserved = true
            }
        }
    }

    /// Whether the poll still has a phase to drive.
    private var isDeviceCodePollable: Bool {
        switch deviceCodePairing {
        case .waitingForHost, .confirming: return true
        default: return false
        }
    }

    private var deviceCodeExpiredMessage: String {
        if case .confirming = deviceCodePairing {
            return "This pairing request expired before it was confirmed. Get a new code."
        }
        return "That code expired before any machine used it. Get a new one."
    }

    /// Approve the machine that entered the code.
    func confirmDeviceCode() {
        guard case .confirming(let requestId, _, _, _, let blocked) = deviceCodePairing else { return }
        guard blocked == nil else { return }
        deviceCodePollTask?.cancel()
        deviceCodePollTask = nil
        deviceCodePairing = .claiming(requestId: requestId)
        Task { [weak self] in
            guard let self else { return }
            let request = DeviceCodeConfirmRequest(
                mobile_device_id: self.deviceIdentityStore.getOrCreateDeviceId(),
                mobile_device_name: self.deviceIdentityStore.getDeviceName(),
                platform: "ios"
            )
            do {
                let response = try await self.agentRepository.confirmDeviceCode(
                    requestId: requestId,
                    request: request
                )
                // Hand off to the shared tail: from here the two pairing
                // directions are the same problem — the relay has the
                // credentials and the agent still has to pick them up.
                self.deviceCodePairing = .idle
                self.deviceCodeSheetPresented = false
                self.pairingSheetPresented = true
                self.startWaitingForOnlineWatcher(
                    agentId: response.agent_id,
                    friendlyName: response.agent_friendly_name,
                    wsDeliveredAtClaim: response.ws_delivered
                )
            } catch {
                self.deviceCodePairing = .error(Self.deviceCodeMessage(for: error))
            }
        }
    }

    /// Abandon the pairing request.
    ///
    /// The cancel call is best-effort: it is a courtesy to the agent waiting on
    /// its WebSocket, and a failure changes nothing the user can act on.
    func dismissDeviceCodePairing() {
        let requestId = deviceCodePairing.requestId
        resetDeviceCodePairing()
        deviceCodeSheetPresented = false
        guard let requestId else { return }
        Task { [agentRepository] in
            try? await agentRepository.cancelDeviceCode(requestId: requestId)
        }
    }

    /// Cancel the poll and reset to idle without touching sheet presentation —
    /// the sheet's `onDismiss` calls this so a swipe-to-close still tears the
    /// poll down cleanly.
    func resetDeviceCodePairing() {
        deviceCodeMintTask?.cancel()
        deviceCodeMintTask = nil
        deviceCodePollTask?.cancel()
        deviceCodePollTask = nil
        deviceCodePairing = .idle
    }

    /// Copy for the reversed direction's failures.
    ///
    /// Separate from the QR flow's messages because the remedies differ.
    static func deviceCodeMessage(for error: Error) -> String {
        guard let apiError = error as? APIError else {
            return "Pairing failed: \(ErrorText.message(for: error))"
        }
        if apiError.errorBody.contains(trustPinMismatchCode) {
            return "This relay's signing key is not the one this account first paired with. "
                + "Pairing is refused. Compare the fingerprint on a machine you already paired "
                + "(mobaide-agent show-trust-fp) and contact the relay operator if they differ."
        }
        switch apiError.code {
        case 404: return "This pairing request no longer exists. Get a new code."
        case 409: return DeviceCodeBlockedReason.ownedByOther.message
        case 410: return "This pairing request expired or was already used. Get a new code."
        case 429: return "Too many pairing attempts. Wait a moment and try again."
        case 503:
            return "Pairing is not configured on this relay — it is missing its manifest "
                + "signing keys. Contact the relay operator."
        default: return "Pairing failed: \(ErrorText.message(for: error))"
        }
    }

    /// Abandon the pairing UI (Confirming cancel / Waiting "Stop waiting" /
    /// Success OK / Error close). Does not undo an already-succeeded claim.
    func dismissPairing() {
        resetPairing()
        pairingSheetPresented = false
    }

    /// Cancel the watcher and reset to idle without touching sheet presentation
    /// — the pairing sheet's `onDismiss` calls this so a swipe-to-close still
    /// tears the watcher down cleanly.
    func resetPairing() {
        watcherTask?.cancel()
        watcherTask = nil
        qrPairing = .idle
    }

    // MARK: - 60 s waiting-for-online watcher

    private func startWaitingForOnlineWatcher(agentId: String, friendlyName: String, wsDeliveredAtClaim: Bool?) {
        watcherTask?.cancel()
        watcherTask = nil
        qrPairing = .waitingForOnline(
            agentId: agentId,
            friendlyName: friendlyName,
            elapsedSeconds: 0,
            wsDeliveredAtClaim: wsDeliveredAtClaim
        )

        // The status_changed event can race ahead of the claim's own REST
        // refresh; if the agent already reads online, succeed immediately.
        if isAgentOnline(agentId) {
            qrPairing = .success(agentId: agentId, friendlyName: friendlyName)
            return
        }

        watcherTask = Task { [weak self] in
            guard let self else { return }
            // Defensive resync in case status_changed landed between the claim
            // refresh and the watcher starting; the poll below still catches
            // WS pushes regardless.
            try? await self.agentRepository.refreshAgents()
            do {
                for elapsed in 1...Self.waitForOnlineTimeoutSeconds {
                    try Task.checkCancellation()
                    try await self.sleeper.sleep(for: .seconds(1))
                    if self.isAgentOnline(agentId) {
                        self.qrPairing = .success(agentId: agentId, friendlyName: friendlyName)
                        return
                    }
                    guard case .waitingForOnline(let waitingId, _, _, let ws) = self.qrPairing,
                          waitingId == agentId
                    else { return }
                    self.qrPairing = .waitingForOnline(
                        agentId: agentId,
                        friendlyName: friendlyName,
                        elapsedSeconds: elapsed,
                        wsDeliveredAtClaim: ws
                    )
                }
                self.qrPairing = .error(
                    Self.timeoutMessage(friendlyName: friendlyName, wsDeliveredAtClaim: wsDeliveredAtClaim)
                )
            } catch {
                // Cancellation (dismiss / re-scan / deinit) unwinds here.
                self.watcherCancellationObserved = true
            }
        }
    }

    private func isAgentOnline(_ agentId: String) -> Bool {
        agentRepository.currentAgents.first { $0.agent_id == agentId }?.online == true
    }

    static func timeoutMessage(friendlyName: String, wsDeliveredAtClaim: Bool?) -> String {
        var parts = ["‘\(friendlyName)’ did not come online within 60 s."]
        switch wsDeliveredAtClaim {
        case .some(true):
            parts.append("The relay delivered credentials to the agent, but its tunnel hasn’t connected yet.")
        case .some(false):
            parts.append("Credentials were staged for the agent’s next reconnect.")
        case .none:
            break
        }
        parts.append("On the agent host, run `mobaide-agent show-qr` to re-render the QR and restart pairing.")
        return parts.joined(separator: " ")
    }
}
