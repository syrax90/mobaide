package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.RestApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Observes the running agent's version vs. the rolled-out manifest.
 *
 * A single ``GET /api/version`` against the authenticated agent yields
 * both fields: ``server_version`` is the agent that is running right now;
 * ``latest_agent_version`` is the manifest pointer that ``central_server``
 * forwarded to the agent at ``auth_ok`` (or via a mid-connection
 * ``agent_manifest_update`` control frame). When they differ, the UI
 * surfaces a non-blocking banner — the staged-binary auto-updater is
 * already applying the update silently once the agent is idle, so the
 * banner exists to make the wait visible (and to expose the
 * "Install now" CTA that calls ``force_update_now`` on the agent).
 *
 * Lives alongside [VersionGateRepository] in the repository layer and
 * surfaces a domain-state ``StateFlow``; not a raw network client.
 *
 * The probe goes through the agent-scoped [RestApi] (not [com.mobaide.app.data.network.CentralApi])
 * so the request carries the mobile-access bearer token via
 * [com.mobaide.app.data.auth.AuthInterceptor]. In relay mode the URL
 * resolves to ``{central}/proxy/{agent_id}/api/version`` which requires
 * that header; routing through [com.mobaide.app.data.network.CentralApi]
 * would 401, and [com.mobaide.app.data.auth.CentralAuthInterceptor]
 * treats any 401 as a terminal session-rejection and logs the user out.
 *
 * DI is wired exclusively through [com.mobaide.app.di.RepositoryModule]
 * (matching every other class in this package) — no class-level Hilt
 * annotations are needed.
 */
class AgentVersionMonitor(
    private val restApi: RestApi,
    private val tokenStore: TokenStore,
) {
    data class Outdated(val currentVersion: String, val latestVersion: String)

    private val _outdated = MutableStateFlow<Outdated?>(null)
    val outdated: StateFlow<Outdated?> = _outdated.asStateFlow()

    /**
     * Re-evaluate the agent version state.
     *
     * Best-effort: connection or parse failures leave the previous state
     * untouched so a transient flap does not blink the banner away.
     * Returns ``true`` when a fresh response was applied, ``false`` when
     * the probe was skipped (no agent base URL) or the request failed.
     */
    suspend fun probe(): Boolean {
        if (tokenStore.getEffectiveBaseUrl() == null) return false
        val info = try {
            restApi.getAgentVersion()
        } catch (_: Throwable) {
            return false
        }
        val latest = info.latest_agent_version
        _outdated.value =
            if (!latest.isNullOrBlank() && latest != info.server_version) {
                Outdated(currentVersion = info.server_version, latestVersion = latest)
            } else {
                null
            }
        return true
    }

    fun reset() {
        _outdated.value = null
    }
}
