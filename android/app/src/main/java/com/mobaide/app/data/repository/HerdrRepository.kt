package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.PaneClient
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.HerdrCloseRequest
import com.mobaide.app.data.network.dto.HerdrCreateRequest
import com.mobaide.app.data.network.dto.HerdrCreateResponse
import com.mobaide.app.data.network.dto.HerdrPaneTextResponse
import com.mobaide.app.data.network.dto.HerdrRenameRequest
import com.mobaide.app.data.network.dto.HerdrSnapshot
import com.mobaide.app.data.network.dto.herdrCapability
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/** A Herdr refusal: the agent's own words and its error code. */
class HerdrException(val code: String?, message: String) : Exception(message) {
    /** The pane is closed or its herdr server stopped: retrying cannot help. */
    val isGone: Boolean get() = code == "pane_not_found" || code == "server_not_found"
}

/**
 * The Herdr servers on the connected agent's computer.
 *
 * The single source of truth for the dashboard: `herdr.changed` carries the
 * whole state on every change and on every events-socket connect, so the UI
 * needs no refetch after an action. [snapshot] is null until the first state
 * arrives and for an agent without the dashboard.
 */
class HerdrRepository(
    private val restApi: RestApi,
    private val eventBus: AgentEventBus,
    private val tokenStore: TokenStore,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _snapshot = MutableStateFlow<HerdrSnapshot?>(null)
    val snapshot: StateFlow<HerdrSnapshot?> = _snapshot.asStateFlow()

    /** The agent the current [snapshot] belongs to. */
    private var loadedAgentId: String? = null

    init {
        scope.launch {
            eventBus.stream<AgentEvent.HerdrChanged>().collect { event ->
                loadedAgentId = tokenStore.getAgentId()
                _snapshot.value = event.snapshot
            }
        }
        scope.launch {
            // `herdr.changed` arrives only while the agent has the dashboard, so
            // a held state is re-checked on each sessions list (sent on every
            // connect). Conflated: the check is a REST read that must not hold
            // back the bus.
            eventBus.stream<AgentEvent.SessionsChanged>().conflate().collect {
                if (_snapshot.value != null) recheckCapability()
            }
        }
    }

    /**
     * Drop the held state when the agent no longer has the dashboard. Reads
     * the version only: the state itself arrives as `herdr.changed`, and a
     * REST copy read here could replace a newer pushed one.
     */
    private suspend fun recheckCapability() {
        val supported = runCatching { restApi.getAgentVersion().herdrCapability() }.getOrNull() ?: return
        if (!supported) _snapshot.value = null
    }

    /** The id of the connected agent; part of every per-device pane key. */
    fun agentId(): String = tokenStore.getAgentId().orEmpty()

    /**
     * Seed [snapshot] over REST. Another agent's snapshot is dropped first. An
     * agent without `capabilities.herdr` clears it. A failed read keeps what
     * this agent sent last: the events stream corrects it.
     */
    suspend fun refresh() {
        val agentId = tokenStore.getAgentId()
        if (agentId != loadedAgentId) {
            // Another agent was selected: its predecessor's servers are not its own.
            loadedAgentId = agentId
            _snapshot.value = null
        }
        // A failed read says nothing: keep what this agent sent last.
        val supported = runCatching { restApi.getAgentVersion().herdrCapability() }.getOrNull() ?: return
        if (!supported) {
            // The agent has no dashboard (any more): its servers must not stay on screen.
            _snapshot.value = null
            return
        }
        runCatching { restApi.getHerdr() }.onSuccess { _snapshot.value = it }
    }

    suspend fun openPane(serverId: String, paneId: String): String =
        call { restApi.openHerdrPane(serverId, paneId).session_id }

    /** Take control of a pane as this app, or give it back. Only the controller can give it back. */
    suspend fun setControl(serverId: String, paneId: String, control: Boolean) =
        call { restApi.setHerdrControl(serverId, paneId, control, PaneClient.id) }

    /**
     * Give a pane back without waiting for the answer. For a screen that is
     * going away: its own scope is cancelled before the call could finish.
     */
    fun releaseControlDetached(serverId: String, paneId: String) {
        scope.launch { runCatching { restApi.setHerdrControl(serverId, paneId, false, PaneClient.id) } }
    }

    suspend fun paneText(serverId: String, paneId: String, lines: Int): HerdrPaneTextResponse =
        call { restApi.getHerdrPaneText(serverId, paneId, lines) }

    /**
     * Create a workspace, a tab or a split. The answer names the new pane, and
     * carries a message when the pane was made and its agent did not start.
     */
    suspend fun create(request: HerdrCreateRequest): HerdrCreateResponse =
        call { restApi.createHerdrTerminal(request) }

    suspend fun startAgent(serverId: String, paneId: String, agent: String) =
        call { restApi.startHerdrAgent(serverId, paneId, agent) }

    suspend fun rename(serverId: String, target: String, id: String, label: String) =
        call { restApi.renameHerdrItem(HerdrRenameRequest(serverId, target, id, label)) }

    suspend fun close(serverId: String, target: String, id: String) =
        call { restApi.closeHerdrItem(HerdrCloseRequest(serverId, target, id)) }

    private suspend fun <T> call(block: suspend () -> T): T =
        try {
            block()
        } catch (e: ApiException) {
            throw herdrException(e)
        } catch (e: java.io.IOException) {
            throw HerdrException(null, "Herdr did not answer. Check your connection and try again.")
        }

    private fun herdrException(e: ApiException): HerdrException {
        // `{"detail": {"code", "message"}}`, or a plain string for a relay error.
        return when (val detail = extractDetail(e.errorBody)) {
            is JsonObject -> HerdrException(
                detail["code"]?.jsonPrimitive?.contentOrNull?.takeIf { it.isNotEmpty() },
                detail["message"]?.jsonPrimitive?.contentOrNull?.takeIf { it.isNotEmpty() }
                    ?: FALLBACK_MESSAGE,
            )
            is String -> HerdrException(null, detail.takeIf { it.isNotBlank() } ?: FALLBACK_MESSAGE)
            else -> HerdrException(null, FALLBACK_MESSAGE)
        }
    }

    private companion object {
        const val FALLBACK_MESSAGE = "Herdr did not answer. Try again."
    }
}
