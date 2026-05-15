package com.mobaide.app.ui.agents

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.BuildConfig
import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.security.TrustKeyPinning
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
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
     * collects `agent.status_changed` events (delivered via [UserEventBus] on
     * `ws/user-events` → [AgentRepository.updateAgentOnline]) until either the
     * agent flips to `online=true` ([Success]) or the timeout expires
     * ([Error]). The timeout error message intentionally enumerates the
     * remediation commands (`mobaide-agent show-qr`, `Get-ScheduledTask`,
     * `Get-Content agent.err.log`) so a Windows operator can act without
     * a docs round-trip.
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
    data class Success(val agentId: String, val friendlyName: String) : QrPairingState
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
    /**
     * True between [AgentListViewModel.onScanQrClicked] and the first
     * [AgentListViewModel.onScanResult] callback. Drives a thin
     * progress indicator on the screen so the cold-start delay of the
     * GMS code-scanner module install does not look like a frozen tap.
     */
    val isScannerLaunching: Boolean = false,
)

@HiltViewModel
class AgentListViewModel @Inject constructor(
    private val agentRepository: AgentRepository,
    private val connectionStore: ConnectionStore,
    private val tokenStore: TokenStore,
    private val sessionManager: SessionManager,
    private val agentEventBus: AgentEventBus,
    private val userIdentityStore: UserIdentityStore,
    private val trustKeyPinning: TrustKeyPinning,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        AgentListUiState(connectedAgentId = tokenStore.getAgentId()),
    )
    val uiState: StateFlow<AgentListUiState> = _uiState.asStateFlow()

    private val _navigateToSessions = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val navigateToSessions: SharedFlow<Unit> = _navigateToSessions.asSharedFlow()

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

    init {
        viewModelScope.launch {
            agentRepository.observeAgents().collect { agents ->
                _uiState.update { it.copy(agents = agents) }
            }
        }
        refresh()
    }

    override fun onCleared() {
        super.onCleared()
        waitForOnlineJob?.cancel()
        waitForOnlineJob = null
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

    fun confirmDelete(agentId: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(deleteConfirmAgent = null) }
            try {
                // If deleting the currently connected agent, disconnect first
                if (agentId == _uiState.value.connectedAgentId) {
                    agentEventBus.stop()
                    sessionManager.agentDisconnect()
                    _uiState.update { it.copy(connectedAgentId = null) }
                }
                agentRepository.deleteAgent(agentId)
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
                val mobileDeviceId = userIdentityStore.getUserId()
                    ?: state.payload.nonce  // Fallback: never empty.
                val response = agentRepository.claimAgentByPairing(
                    nonce = state.payload.nonce,
                    trustKeyFp = state.payload.trustKeyFp,
                    mobileDeviceId = mobileDeviceId,
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
     * watcher first — re-entrancy guard for a double-claim attempt or a
     * rapid dismiss/re-scan.
     *
     * Two parallel signals decide the outcome:
     *  * `online=true` in [AgentRepository.observeAgents] — pushed by
     *    [UserEventBus] from the relay's `agent.status_changed` event when
     *    the agent's `/ws/agent-tunnel` connects. This is the authoritative
     *    "actually paired" signal.
     *  * 1 s tick — emits an updated `elapsedSeconds` for the UI counter
     *    and short-circuits to [QrPairingState.Error] at
     *    [WAIT_FOR_ONLINE_TIMEOUT_SECONDS].
     *
     * One extra `refreshAgents()` is fired as a defensive resync: the
     * [AgentRepository.claimAgentByPairing] call already does one, but a
     * `agent.status_changed=true` event delivered between those two calls
     * would be re-overwritten by the stale REST snapshot otherwise.
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
        // Snapshot the just-claimed agent's online status in case the
        // status_changed event raced ahead of the REST snapshot inside
        // claimAgentByPairing — observed in CI on fast loopback networks.
        val seedOnline = _uiState.value.agents
            .firstOrNull { it.agent_id == agentId }?.online == true
        if (seedOnline) {
            _uiState.update {
                it.copy(qrPairing = QrPairingState.Success(agentId, friendlyName))
            }
            return
        }
        waitForOnlineJob = viewModelScope.launch {
            try {
                agentRepository.refreshAgents()
            } catch (e: kotlin.coroutines.cancellation.CancellationException) {
                // Structured concurrency requires CancellationException to
                // propagate. A bare ``catch (_: Throwable)`` would swallow it
                // and let the coroutine continue into the for-loop below;
                // the subsequent ``delay`` would re-throw, but any non-
                // suspending side-effect added between here and that delay
                // would execute in a "zombie" coroutine — exactly the
                // future-edit footgun that motivates re-throwing here.
                throw e
            } catch (_: Throwable) {
                // Best-effort resync; the watcher loop still observes the
                // flow for status_changed pushes regardless.
            }
            for (elapsed in 1..WAIT_FOR_ONLINE_TIMEOUT_SECONDS) {
                delay(1000L)
                val current = _uiState.value
                val online = current.agents
                    .firstOrNull { it.agent_id == agentId }?.online == true
                if (online) {
                    _uiState.update {
                        it.copy(qrPairing = QrPairingState.Success(agentId, friendlyName))
                    }
                    return@launch
                }
                val pairing = current.qrPairing
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
                    qrPairing = QrPairingState.Error(
                        buildPairingTimeoutMessage(
                            agentId = agentId,
                            friendlyName = friendlyName,
                            wsDeliveredAtClaim = wsDeliveredAtClaim,
                        ),
                    ),
                )
            }
        }
    }

    private fun buildPairingTimeoutMessage(
        agentId: String,
        friendlyName: String,
        wsDeliveredAtClaim: Boolean?,
    ): String {
        val shortId = agentId.take(8)
        val baseMessage = "Agent $friendlyName ($shortId…) did not come online " +
            "within $WAIT_FOR_ONLINE_TIMEOUT_SECONDS seconds. " +
            "The pairing request was accepted by the server, but the agent " +
            "never connected back."
        val dispatchHint = when (wsDeliveredAtClaim) {
            true -> " The server reports the credential push was delivered " +
                "to the agent's WebSocket, so the agent likely received " +
                "credentials but failed to start its relay client " +
                "(check the agent host's network/firewall to the relay)."
            false -> " The server reports the agent's WebSocket was NOT open " +
                "at claim time; credentials were staged for the agent's " +
                "next reconnect (check that the agent task is running)."
            null -> ""
        }
        val remediation = " On the agent host: `mobaide-agent show-qr` " +
            "(re-render QR), `Get-ScheduledTask MobaideAgent` (task state), " +
            "`Get-Content \"\$env:ProgramFiles\\MobaideAgent\\logs\\agent.err.log\" -Tail 50`."
        return baseMessage + dispatchHint + remediation
    }

    fun connectToAgent(agent: AgentSummaryResponse) {
        if (_uiState.value.isConnecting) return
        if (agent.agent_id == _uiState.value.connectedAgentId) {
            // Already connected — just navigate
            _navigateToSessions.tryEmit(Unit)
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(isConnecting = true, error = null) }
            try {
                if (sessionManager.isLoggedIn()) {
                    agentEventBus.stop()
                    sessionManager.agentDisconnect()
                }

                val tokens = agentRepository.getMobileTokensForAgent(agent.agent_id)
                val userId = userIdentityStore.getUserId() ?: agent.agent_id

                tokenStore.saveRelayCredentials(
                    serverUrl = BuildConfig.CENTRAL_SERVER_URL,
                    agentId = agent.agent_id,
                    pairingId = userId,
                    deviceId = userId,
                    accessToken = tokens.access_token,
                    refreshToken = tokens.refresh_token,
                )

                sessionManager.markAgentConnected()

                val existingProfile = connectionStore.listProfiles()
                    .find { it.agentId == agent.agent_id && it.mode == "relay" }
                if (existingProfile != null) {
                    // Update existing profile with fresh tokens
                    connectionStore.saveProfile(
                        existingProfile.copy(
                            accessToken = tokens.access_token,
                            refreshToken = tokens.refresh_token,
                        ),
                    )
                    connectionStore.setActiveProfileId(existingProfile.id)
                } else {
                    connectionStore.captureActiveProfile(
                        tokenStore,
                        label = "Agent: ${agent.name}",
                    )
                }

                agentEventBus.start()

                _uiState.update {
                    it.copy(
                        isConnecting = false,
                        connectedAgentId = agent.agent_id,
                    )
                }

                _navigateToSessions.tryEmit(Unit)
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        isConnecting = false,
                        error = e.message ?: "Failed to connect to agent",
                    )
                }
            }
        }
    }
}
