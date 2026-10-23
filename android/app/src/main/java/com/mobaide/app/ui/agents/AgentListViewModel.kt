package com.mobaide.app.ui.agents

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.DeviceIdentityStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.AgentSwitchCoordinator
import com.mobaide.app.data.security.TrustKeyPinning
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/** Sub-state for the QR pairing flow. Token is never stored here. */
sealed interface QrPairingState {
    data object Idle : QrPairingState
    data class Confirming(val payload: ParsedQrPairingPayload) : QrPairingState
    data class Claiming(val payload: ParsedQrPairingPayload) : QrPairingState

    /**
     * Server accepted the claim; mobile is now waiting for the agent's relay
     * tunnel to come online. The previous design transitioned straight to
     * [Success] on the claim HTTP 200, but the 200 only confirms the central
     * server persisted the credentials — it says nothing about whether the
     * agent's WS received them, whether the agent's `.env` write succeeded,
     * or whether the agent's relay client successfully dialled
     * `/ws/agent-tunnel`. Without this intermediate state the UI lied: a
     * fully-broken agent host (firewall, ACL, credential write failure)
     * was shown as "Paired" with no error surface.
     *
     * The watcher coroutine in [AgentListViewModel.startWaitingForOnlineWatcher]
     * observes the cached agent list — fed by `agent.status_changed` events
     * (delivered via [UserEventBus] on `ws/user-events` →
     * [AgentRepository.updateAgentOnline]) and by a periodic REST resync —
     * until either the agent flips to `online=true` ([Success]) or the
     * timeout expires ([WaitTimedOut]).
     *
     * [wsDeliveredAtClaim] is the server's optional hint: `true` means
     * the agent's WS was registered at claim time and the credential push
     * was dispatched directly; `false` means it was staged for the agent's
     * next reconnect to recover. `null` covers servers older than the
     * `ws_delivered` rollout. The mobile uses it only to refine the
     * timeout-error copy.
     */
    data class WaitingForOnline(
        val agentId: String,
        val friendlyName: String,
        val elapsedSeconds: Int = 0,
        val wsDeliveredAtClaim: Boolean? = null,
    ) : QrPairingState

    /**
     * The [WaitingForOnline] budget ran out with the agent still offline.
     * Not a failure: the claim succeeded and the agent stays paired to the
     * account; it simply has not connected back yet. The user can keep
     * waiting ([AgentListViewModel.keepWaiting]) or close the dialog and
     * watch the list, which flips the agent Online on its own.
     */
    data class WaitTimedOut(
        val agentId: String,
        val friendlyName: String,
        val wsDeliveredAtClaim: Boolean?,
    ) : QrPairingState
    data class Success(val agentId: String, val friendlyName: String) : QrPairingState

    /** A claim that was refused or could not be sent. */
    data class Error(val message: String) : QrPairingState
}

/**
 * How long the mobile waits for the agent to come online after a successful
 * claim before declaring the pairing failed. End-to-end happy path is
 * usually <5s (claim push → .env write → relay client dial → tunnel auth →
 * `_broadcast_status(online=true)`); the agent's inner write-retry loop
 * caps at a 60s ceiling so anything beyond that signals a hard failure
 * (write keeps failing, tunnel can't connect, etc.). Pulled out as a
 * named constant so tests can reference the same value.
 */
internal const val WAIT_FOR_ONLINE_TIMEOUT_SECONDS: Int = 60

/**
 * How often the waiting-for-online watcher re-reads `GET /api/agents` while
 * it waits. The `agent.status_changed` push stays the primary signal; this
 * bounded resync (≤12 requests over the 60 s window, the same cadence as
 * web's `DEFENSIVE_REFETCH_MS`) is what recovers when that push is lost —
 * the user socket was down or still reconnecting when the relay emitted it,
 * the event named an agent not yet in the cache, or a whole-list refresh in
 * flight overwrote the flag it had just set. The relay computes `online`
 * from its live routing table, so the poll is authoritative.
 */
internal const val DEFENSIVE_REFETCH_SECONDS: Int = 5

internal const val DEVICE_CODE_POLL_INTERVAL_MS: Long = 2_000L

/** The `detail.error` discriminator on the relay's account-trust-pin refusal. */
internal const val TRUST_PIN_MISMATCH_CODE: String = "trust_key_pin_mismatch"

/**
 * Body of the "Agent not online yet" dialog. Same wording as iOS
 * (`AgentListModel.waitTimeoutMessage`) and web (`waitTimeoutMessage`), and
 * deliberately neutral about the agent host: the phone cannot tell whether
 * it is a Windows task, a systemd unit, or a launchd job.
 */
internal fun buildWaitTimeoutMessage(
    friendlyName: String,
    wsDeliveredAtClaim: Boolean?,
): String {
    val parts = mutableListOf(
        "‘$friendlyName’ was paired to your account but hasn't connected within " +
            "$WAIT_FOR_ONLINE_TIMEOUT_SECONDS s. It will show as Online in your agents " +
            "list as soon as its relay client connects.",
    )
    when (wsDeliveredAtClaim) {
        true -> parts += "The relay delivered credentials to the agent, but its tunnel " +
            "hasn't connected yet."
        false -> parts += "Credentials were staged for the agent's next reconnect."
        null -> Unit
    }
    parts += "Check that the agent service is running on that host, or run " +
        "`mobaide-agent show-qr` there to restart pairing."
    return parts.joinToString(" ")
}

data class AgentListUiState(
    val agents: List<AgentSummaryResponse> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val addDialogState: AddAgentDialogState = AddAgentDialogState.Hidden,
    val reRegisterDialogState: ReRegisterDialogState = ReRegisterDialogState.Hidden,
    val deleteConfirmAgent: AgentSummaryResponse? = null,
    val isConnecting: Boolean = false,
    val connectedAgentId: String? = null,
    val qrPairing: QrPairingState = QrPairingState.Idle,
    val deviceCodePairing: DeviceCodePairingState = DeviceCodePairingState.Idle,
    /**
     * True between [AgentListViewModel.onScanQrClicked] and the first
     * [AgentListViewModel.onScanResult] callback. Drives a thin
     * progress indicator on the screen so the cold-start delay of the
     * GMS code-scanner module install does not look like a frozen tap.
     */
    val isScannerLaunching: Boolean = false,
    val showInstallSheet: Boolean = false,
)

@HiltViewModel
class AgentListViewModel @Inject constructor(
    private val agentRepository: AgentRepository,
    private val tokenStore: TokenStore,
    private val sessionManager: SessionManager,
    private val agentEventBus: AgentEventBus,
    private val deviceIdentityStore: DeviceIdentityStore,
    private val trustKeyPinning: TrustKeyPinning,
    private val agentSwitchCoordinator: AgentSwitchCoordinator,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        AgentListUiState(connectedAgentId = tokenStore.getAgentId()),
    )
    val uiState: StateFlow<AgentListUiState> = _uiState.asStateFlow()

    private val _navigateToSessions = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val navigateToSessions: SharedFlow<Unit> = _navigateToSessions.asSharedFlow()

    private val _messages = Channel<String>(Channel.BUFFERED)

    /** One-shot confirmations for the screen's snackbar. */
    val messages: Flow<String> = _messages.receiveAsFlow()

    private val _scanRequests = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val scanRequests: SharedFlow<Unit> = _scanRequests.asSharedFlow()

    /**
     * Watcher job for the [QrPairingState.WaitingForOnline] phase. Held on
     * the ViewModel (not inside the state object) so every transition out
     * of `WaitingForOnline` — success, timeout, user-dismiss, scan-restart —
     * can cancel it from a single place. Re-assigning this field cancels
     * the previous job under the lock implicit in `viewModelScope`'s single
     * `Dispatchers.Main.immediate` execution.
     */
    private var waitForOnlineJob: Job? = null

    /**
     * Poll job for the device-code flow. Held here for the same reason as
     * [waitForOnlineJob]: every exit from the polling phase — a machine
     * arrives, the code expires, the user backs out — cancels it from one
     * place.
     */
    private var deviceCodePollJob: Job? = null

    /**
     * Wall clock the device-code poll measures its deadline against.
     *
     * Overridable because `delay` inside a `runTest` advances only the
     * scheduler's virtual clock — a loop that compares
     * `System.currentTimeMillis()` against a deadline therefore never
     * terminates under `advanceUntilIdle`, since each iteration schedules
     * another `delay` and the real clock barely moves. Tests point this at
     * `testScheduler.currentTime` so virtual time drives the deadline too.
     * iOS carries the same seam as `AgentListModel.now`.
     */
    internal var nowMillis: () -> Long = { System.currentTimeMillis() }

    init {
        viewModelScope.launch {
            agentRepository.observeAgents().collect { agents ->
                _uiState.update { it.copy(agents = agents) }
            }
        }
        // Before the collectors below: `refresh()` clears `error` inline, and a
        // switch that already failed (airplane mode) has its message buffered
        // and waiting — delivered first, it would be wiped before the screen
        // ever saw it.
        refresh()
        // Every switch lands here (its disconnect resets the stack onto this
        // screen), so the connecting state, the connected mark and the failure
        // message all come from the coordinator rather than from `init`.
        viewModelScope.launch {
            agentSwitchCoordinator.errors.collect { message ->
                _uiState.update { it.copy(error = message) }
            }
        }
        viewModelScope.launch {
            agentSwitchCoordinator.switchingAgentId.collect { switching ->
                _uiState.update {
                    it.copy(
                        isConnecting = switching != null,
                        connectedAgentId = if (switching == null) tokenStore.getAgentId() else it.connectedAgentId,
                    )
                }
            }
        }
    }

    override fun onCleared() {
        super.onCleared()
        waitForOnlineJob?.cancel()
        waitForOnlineJob = null
        cancelDeviceCodePoll()
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                agentRepository.refreshAgents()
            } catch (e: Exception) {
                _uiState.update { it.copy(error = e.message ?: "Failed to load agents") }
            } finally {
                _uiState.update { it.copy(isLoading = false) }
            }
        }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }

    fun showAddDialog() {
        _uiState.update { it.copy(addDialogState = AddAgentDialogState.Input) }
    }

    fun dismissAddDialog() {
        _uiState.update { it.copy(addDialogState = AddAgentDialogState.Hidden) }
    }

    fun showInstallSheet() {
        _uiState.update { it.copy(showInstallSheet = true) }
    }

    fun dismissInstallSheet() {
        _uiState.update { it.copy(showInstallSheet = false) }
    }

    fun createAgent(name: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(addDialogState = AddAgentDialogState.Loading) }
            try {
                val result = agentRepository.createAgent(name)
                _uiState.update {
                    it.copy(
                        addDialogState = AddAgentDialogState.ShowingCredentials(
                            agentId = result.agent_id,
                            token = result.token,
                        ),
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        addDialogState = AddAgentDialogState.Hidden,
                        error = e.message ?: "Failed to create agent",
                    )
                }
            }
        }
    }

    fun showReRegisterDialog(agent: AgentSummaryResponse) {
        _uiState.update {
            it.copy(
                reRegisterDialogState = ReRegisterDialogState.Confirming(
                    agentId = agent.agent_id,
                    agentName = agent.name,
                ),
            )
        }
    }

    fun dismissReRegisterDialog() {
        _uiState.update { it.copy(reRegisterDialogState = ReRegisterDialogState.Hidden) }
    }

    fun confirmReRegister(agentId: String) {
        viewModelScope.launch {
            _uiState.update {
                it.copy(reRegisterDialogState = ReRegisterDialogState.Loading(agentId))
            }
            try {
                val result = agentRepository.reregisterAgent(agentId)
                _uiState.update {
                    it.copy(
                        reRegisterDialogState = ReRegisterDialogState.ShowingCredentials(
                            agentId = result.agent_id,
                            token = result.token,
                        ),
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        reRegisterDialogState = ReRegisterDialogState.Hidden,
                        error = e.message ?: "Failed to re-register agent",
                    )
                }
            }
        }
    }

    fun showDeleteConfirm(agent: AgentSummaryResponse) {
        _uiState.update { it.copy(deleteConfirmAgent = agent) }
    }

    fun dismissDeleteConfirm() {
        _uiState.update { it.copy(deleteConfirmAgent = null) }
    }

    fun confirmDelete(agentId: String, uninstall: Boolean = false) {
        val agent = _uiState.value.deleteConfirmAgent?.takeIf { it.agent_id == agentId }
        viewModelScope.launch {
            _uiState.update { it.copy(deleteConfirmAgent = null) }
            try {
                // If deleting the currently connected agent, disconnect first
                if (agentId == _uiState.value.connectedAgentId) {
                    agentEventBus.stop()
                    sessionManager.agentDisconnect()
                    _uiState.update { it.copy(connectedAgentId = null) }
                }
                agentRepository.deleteAgent(agentId, uninstall)
                if (uninstall && agent != null) {
                    _messages.send(
                        if (agent.online) {
                            "“${agent.name}” is uninstalling itself."
                        } else {
                            "“${agent.name}” will uninstall itself when it next comes online."
                        },
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(error = e.message ?: "Failed to delete agent")
                }
            }
        }
    }

    fun onScanQrClicked() {
        if (_uiState.value.isScannerLaunching) return
        cancelWaitForOnlineWatcher()
        _uiState.update {
            it.copy(qrPairing = QrPairingState.Idle, isScannerLaunching = true)
        }
        _scanRequests.tryEmit(Unit)
    }

    fun dismissQrPairing() {
        cancelWaitForOnlineWatcher()
        _uiState.update { it.copy(qrPairing = QrPairingState.Idle) }
    }

    /** Give the agent another [WAIT_FOR_ONLINE_TIMEOUT_SECONDS] after a [QrPairingState.WaitTimedOut]. */
    fun keepWaiting() {
        val state = _uiState.value.qrPairing as? QrPairingState.WaitTimedOut ?: return
        startWaitingForOnlineWatcher(
            agentId = state.agentId,
            friendlyName = state.friendlyName,
            wsDeliveredAtClaim = state.wsDeliveredAtClaim,
        )
    }

    /** Mint a pairing code and start watching for a machine to enter it. */
    fun startDeviceCodePairing() {
        if (_uiState.value.deviceCodePairing !is DeviceCodePairingState.Idle) return
        _uiState.update { it.copy(deviceCodePairing = DeviceCodePairingState.Minting) }
        viewModelScope.launch {
            try {
                val minted = agentRepository.mintDeviceCode()
                _uiState.update {
                    it.copy(
                        deviceCodePairing = DeviceCodePairingState.WaitingForHost(
                            requestId = minted.request_id,
                            code = minted.code,
                            command = minted.verification_command,
                        ),
                    )
                }
                startDeviceCodePoll(minted.request_id, minted.expires_at)
            } catch (e: kotlin.coroutines.cancellation.CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        deviceCodePairing = DeviceCodePairingState.Error(
                            deviceCodeErrorMessage(e)
                        ),
                    )
                }
            }
        }
    }

    /**
     * Poll until a machine redeems the code, or the code dies.
     *
     * Polled rather than pushed, unlike everything else in this ViewModel that
     * reads from [AgentEventBus]: a redemption is a step inside a handshake,
     * not a change to any entity a `*.changed` event describes, and inventing
     * a server event for it would put a per-account channel behind a
     * two-minute foreground interaction. Same shape as the bounded loop in
     * [startWaitingForOnlineWatcher], and it stops the moment the request
     * settles.
     */
    private fun startDeviceCodePoll(requestId: String, expiresAt: String) {
        cancelDeviceCodePoll()
        // Bounded by the request's own deadline, not by `while (true)`: a
        // client that loses connectivity with the dialog open would otherwise
        // wake the radio every two seconds for the life of the process.
        //
        // Re-read on every poll because the deadline *moves*: before redemption
        // it is the code's expiry, and once a machine redeems, the relay swaps
        // in the nonce's — a different, later clock. Pinning the mint value
        // would declare a still-confirmable request expired.
        var deadline = parseInstantMillis(expiresAt)
        deviceCodePollJob = viewModelScope.launch {
            // `let` binds a stable local: `deadline` is reassigned from inside
            // this closure, so it cannot smart-cast in the condition itself.
            while (deadline.let { it == null || nowMillis() < it }) {
                delay(DEVICE_CODE_POLL_INTERVAL_MS)
                val status = try {
                    agentRepository.getDeviceCodeStatus(requestId)
                } catch (e: kotlin.coroutines.cancellation.CancellationException) {
                    throw e
                } catch (_: Exception) {
                    // A dropped poll is not a failed pairing: the code is
                    // still live server-side, so keep polling rather than
                    // tearing down a request the user can still complete.
                    continue
                }
                // Polling continues through Confirming, deliberately. The
                // confirm window is the nonce's, so a user who sits on the
                // screen past it would otherwise be looking at a live button
                // whose only possible outcome is a 410.
                val current = _uiState.value.deviceCodePairing
                if (current !is DeviceCodePairingState.WaitingForHost &&
                    current !is DeviceCodePairingState.Confirming
                ) {
                    return@launch
                }
                parseInstantMillis(status.expires_at)?.let { deadline = it }
                when (status.status) {
                    "redeemed" -> {
                        _uiState.update {
                            it.copy(
                                deviceCodePairing = DeviceCodePairingState.Confirming(
                                    requestId = requestId,
                                    hostname = status.hostname ?: "an unnamed machine",
                                    os = status.os,
                                    repairAgentName = status.repair_agent_name,
                                    blockedReason = DeviceCodeBlockedReason
                                        .fromWire(status.blocked_reason),
                                ),
                            )
                        }
                    }
                    "expired", "canceled" -> {
                        val confirming = current is DeviceCodePairingState.Confirming
                        _uiState.update {
                            it.copy(
                                deviceCodePairing = DeviceCodePairingState.Error(
                                    if (confirming) {
                                        "This pairing request expired before it was " +
                                            "confirmed. Get a new code."
                                    } else {
                                        "That code expired before any machine used " +
                                            "it. Get a new one."
                                    }
                                ),
                            )
                        }
                        return@launch
                    }
                }
            }
            if (_uiState.value.deviceCodePairing !is DeviceCodePairingState.Claiming) {
                _uiState.update {
                    it.copy(
                        deviceCodePairing = DeviceCodePairingState.Error(
                            "This pairing request expired. Get a new code."
                        ),
                    )
                }
            }
        }
    }

    /** ISO-8601 to epoch millis, or null when the relay sends a shape we cannot parse. */
    private fun parseInstantMillis(value: String): Long? = try {
        java.time.OffsetDateTime.parse(value).toInstant().toEpochMilli()
    } catch (_: java.time.format.DateTimeParseException) {
        null
    }

    /** Approve the machine that entered the code. */
    fun confirmDeviceCode() {
        val state = _uiState.value.deviceCodePairing
        if (state !is DeviceCodePairingState.Confirming) return
        if (state.blockedReason != null) return
        cancelDeviceCodePoll()
        viewModelScope.launch {
            _uiState.update {
                it.copy(
                    deviceCodePairing = DeviceCodePairingState.Claiming(state.requestId),
                )
            }
            try {
                val response = agentRepository.confirmDeviceCode(
                    requestId = state.requestId,
                    mobileDeviceId = deviceIdentityStore.getOrCreateDeviceId(),
                    mobileDeviceName = deviceIdentityStore.getDeviceName(),
                    friendlyName = null,
                )
                // Hand off to the shared tail: from here the two pairing
                // directions are the same problem — the relay has the
                // credentials and the agent still has to pick them up.
                _uiState.update {
                    it.copy(deviceCodePairing = DeviceCodePairingState.Idle)
                }
                startWaitingForOnlineWatcher(
                    agentId = response.agent_id,
                    friendlyName = response.agent_friendly_name,
                    wsDeliveredAtClaim = response.ws_delivered,
                )
            } catch (e: kotlin.coroutines.cancellation.CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        deviceCodePairing = DeviceCodePairingState.Error(
                            deviceCodeErrorMessage(e)
                        ),
                    )
                }
            }
        }
    }

    /**
     * Abandon the pairing request.
     *
     * The cancel call is best-effort: it is a courtesy to the agent waiting on
     * its WebSocket, and a failure changes nothing the user can act on.
     */
    fun dismissDeviceCodePairing() {
        cancelDeviceCodePoll()
        val current = _uiState.value.deviceCodePairing
        val requestId = when (current) {
            is DeviceCodePairingState.WaitingForHost -> current.requestId
            is DeviceCodePairingState.Confirming -> current.requestId
            else -> null
        }
        _uiState.update { it.copy(deviceCodePairing = DeviceCodePairingState.Idle) }
        if (requestId != null) {
            viewModelScope.launch {
                try {
                    agentRepository.cancelDeviceCode(requestId)
                } catch (e: kotlin.coroutines.cancellation.CancellationException) {
                    throw e
                } catch (_: Exception) {
                }
            }
        }
    }

    private fun cancelDeviceCodePoll() {
        deviceCodePollJob?.cancel()
        deviceCodePollJob = null
    }

    /**
     * Copy for the reversed direction's failures.
     *
     * Separate from the QR flow's messages because the remedies differ.
     */
    private fun deviceCodeErrorMessage(e: Exception): String {
        if (e !is ApiException) return e.message ?: "Pairing failed"
        return when {
            e.errorBody.contains(TRUST_PIN_MISMATCH_CODE) ->
                "This relay's signing key is not the one this account first paired " +
                    "with. Pairing is refused. Compare the fingerprint on a machine " +
                    "you already paired (mobaide-agent show-trust-fp) and contact the " +
                    "relay operator if they differ."
            e.code == 404 -> "This pairing request no longer exists. Get a new code."
            e.code == 409 -> DeviceCodeBlockedReason.OWNED_BY_OTHER.message
            e.code == 410 ->
                "This pairing request expired or was already used. Get a new code."
            e.code == 429 -> "Too many pairing attempts. Wait a moment and try again."
            e.code == 503 ->
                "Pairing is not configured on this relay — it is missing its manifest " +
                    "signing keys. Contact the relay operator."
            else -> e.message ?: "Pairing failed (HTTP ${e.code})"
        }
    }

    private fun cancelWaitForOnlineWatcher() {
        waitForOnlineJob?.cancel()
        waitForOnlineJob = null
    }

    /**
     * Handle a scanned QR (or scan failure). Validates expiry, performs the
     * TOFS trust-key check, then transitions to [QrPairingState.Confirming]
     * so the user can confirm before the network call. Confirmation drives
     * [confirmQrClaim].
     */
    fun onScanResult(result: Result<ParsedQrPairingPayload>) {
        _uiState.update { it.copy(isScannerLaunching = false) }
        result.fold(
            onSuccess = { payload ->
                viewModelScope.launch {
                    val nowSec = System.currentTimeMillis() / 1000L
                    if (payload.expUnix <= nowSec) {
                        _uiState.update {
                            it.copy(
                                qrPairing = QrPairingState.Error(
                                    "QR expired. Render a fresh one on the agent."
                                ),
                            )
                        }
                        return@launch
                    }
                    when (val verdict = trustKeyPinning.verifyOrPin(
                        payload.relayUrl, payload.trustKeyFp,
                    )) {
                        is TrustKeyPinning.VerifyResult.Mismatch -> {
                            _uiState.update {
                                it.copy(
                                    qrPairing = QrPairingState.Error(
                                        "Trust key changed for ${payload.relayUrl} " +
                                        "— possible MITM. Refusing to claim. " +
                                        "If you intentionally rotated the relay key, " +
                                        "clear app data to re-pin."
                                    ),
                                )
                            }
                        }
                        TrustKeyPinning.VerifyResult.Match,
                        TrustKeyPinning.VerifyResult.Pinned -> {
                            _uiState.update {
                                it.copy(
                                    qrPairing = QrPairingState.Confirming(payload),
                                )
                            }
                        }
                    }
                }
            },
            onFailure = { exc ->
                if (exc is CancellationException) {
                    _uiState.update { it.copy(qrPairing = QrPairingState.Idle) }
                } else {
                    _uiState.update {
                        it.copy(
                            qrPairing = QrPairingState.Error(
                                exc.message ?: "Could not scan QR"
                            ),
                        )
                    }
                }
            },
        )
    }

    fun confirmQrClaim() {
        val state = _uiState.value.qrPairing
        if (state !is QrPairingState.Confirming) return
        viewModelScope.launch {
            _uiState.update { it.copy(qrPairing = QrPairingState.Claiming(state.payload)) }
            try {
                val mobileDeviceId = deviceIdentityStore.getOrCreateDeviceId()
                val mobileDeviceName = deviceIdentityStore.getDeviceName()
                val response = agentRepository.claimAgentByPairing(
                    nonce = state.payload.nonce,
                    trustKeyFp = state.payload.trustKeyFp,
                    mobileDeviceId = mobileDeviceId,
                    mobileDeviceName = mobileDeviceName,
                    hostnameHint = null,
                    friendlyName = null,
                )
                // 200 from /api/pairing/claim only confirms the server
                // persisted credentials. The agent may still fail to
                // receive, validate, or write them — see WaitingForOnline's
                // KDoc for the full chain of post-claim failure modes.
                startWaitingForOnlineWatcher(
                    agentId = response.agent_id,
                    friendlyName = response.agent_friendly_name,
                    wsDeliveredAtClaim = response.ws_delivered,
                )
            } catch (e: ApiException) {
                val msg = if (e.code == 410) {
                    "Pairing code consumed or expired. Render a fresh QR on the agent."
                } else {
                    e.message ?: "Pairing failed (HTTP ${e.code})"
                }
                _uiState.update { it.copy(qrPairing = QrPairingState.Error(msg)) }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        qrPairing = QrPairingState.Error(
                            e.message ?: "Pairing failed"
                        ),
                    )
                }
            }
        }
    }

    /**
     * Drive the [QrPairingState.WaitingForOnline] phase. Cancels any prior
     * watcher first — re-entrancy guard for a double-claim attempt, a rapid
     * dismiss/re-scan, or [keepWaiting].
     *
     * Three signals decide the outcome:
     *  * `online=true` in [AgentRepository.observeAgents] — pushed by
     *    [UserEventBus] from the relay's `agent.status_changed` event when
     *    the agent's `/ws/agent-tunnel` connects. The primary signal.
     *  * A REST resync every [DEFENSIVE_REFETCH_SECONDS] and on the final
     *    tick, so a lost push can never strand the dialog and the deadline
     *    verdict is read fresh from the relay, never from a stale cache.
     *  * 1 s tick — emits an updated `elapsedSeconds` for the UI counter
     *    and ends in [QrPairingState.WaitTimedOut] at
     *    [WAIT_FOR_ONLINE_TIMEOUT_SECONDS].
     *
     * One extra `refreshAgents()` runs before the loop: the
     * [AgentRepository.claimAgentByPairing] call already does one, but a
     * `agent.status_changed=true` event delivered between those two calls
     * would be re-overwritten by the stale REST snapshot otherwise.
     *
     * Every online check reads the repository flow directly rather than
     * `_uiState.agents`: that copy is filled by a separate collector, so a
     * refresh followed by an immediate check would still see the previous
     * list.
     */
    private fun startWaitingForOnlineWatcher(
        agentId: String,
        friendlyName: String,
        wsDeliveredAtClaim: Boolean?,
    ) {
        cancelWaitForOnlineWatcher()
        _uiState.update {
            it.copy(
                qrPairing = QrPairingState.WaitingForOnline(
                    agentId = agentId,
                    friendlyName = friendlyName,
                    elapsedSeconds = 0,
                    wsDeliveredAtClaim = wsDeliveredAtClaim,
                ),
            )
        }
        // The status_changed event may have raced ahead of the REST snapshot
        // inside claimAgentByPairing — observed in CI on fast loopback networks.
        if (isAgentOnline(agentId)) {
            _uiState.update {
                it.copy(qrPairing = QrPairingState.Success(agentId, friendlyName))
            }
            return
        }
        waitForOnlineJob = viewModelScope.launch {
            refreshAgentsBestEffort()
            for (elapsed in 1..WAIT_FOR_ONLINE_TIMEOUT_SECONDS) {
                delay(1000L)
                if (elapsed % DEFENSIVE_REFETCH_SECONDS == 0 ||
                    elapsed == WAIT_FOR_ONLINE_TIMEOUT_SECONDS
                ) {
                    refreshAgentsBestEffort()
                }
                if (isAgentOnline(agentId)) {
                    _uiState.update {
                        it.copy(qrPairing = QrPairingState.Success(agentId, friendlyName))
                    }
                    return@launch
                }
                val pairing = _uiState.value.qrPairing
                if (pairing is QrPairingState.WaitingForOnline &&
                    pairing.agentId == agentId
                ) {
                    _uiState.update {
                        it.copy(qrPairing = pairing.copy(elapsedSeconds = elapsed))
                    }
                } else {
                    return@launch
                }
            }
            _uiState.update {
                it.copy(
                    qrPairing = QrPairingState.WaitTimedOut(
                        agentId = agentId,
                        friendlyName = friendlyName,
                        wsDeliveredAtClaim = wsDeliveredAtClaim,
                    ),
                )
            }
        }
    }

    private fun isAgentOnline(agentId: String): Boolean =
        agentRepository.observeAgents().value
            .firstOrNull { it.agent_id == agentId }?.online == true

    private suspend fun refreshAgentsBestEffort() {
        try {
            agentRepository.refreshAgents()
        } catch (e: kotlin.coroutines.cancellation.CancellationException) {
            // Structured concurrency requires CancellationException to
            // propagate. A bare ``catch (_: Throwable)`` would swallow it
            // and let the watcher loop continue; the next ``delay`` would
            // re-throw, but any non-suspending side-effect added before it
            // would execute in a "zombie" coroutine.
            throw e
        } catch (_: Throwable) {
            // Best-effort resync; the watcher loop still observes the
            // flow for status_changed pushes regardless.
        }
    }

    /**
     * Connects to [agent] and opens its sessions list. The switch runs on the
     * coordinator's app scope, never here: its disconnect resets the back
     * stack and destroys this ViewModel mid-flight, and a credential rewrite
     * cancelled half-way would leave the user signed in with no agent.
     */
    fun connectToAgent(agent: AgentSummaryResponse) {
        if (_uiState.value.isConnecting) return
        if (agent.agent_id == _uiState.value.connectedAgentId) {
            // Already connected — just navigate
            _navigateToSessions.tryEmit(Unit)
            return
        }
        _uiState.update { it.copy(error = null) }
        agentSwitchCoordinator.switchAndOpenSessions(agent)
    }
}
