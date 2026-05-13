package com.mobaide.app.data.repository

import com.mobaide.app.BuildConfig
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.VersionInfoResponse
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Probe ``GET /api/version`` BEFORE any WebSocket connect and decide whether
 * the running app can talk to the server it's pointing at.
 *
 * The contract is a single integer ``protocol_version`` compared against the
 * server-declared range ``[min_client_version, max(protocol_versions)]``.
 * Out-of-range → [ServerCompatibility.Incompatible] → the UI replaces the
 * entire navigation graph with [com.mobaide.app.ui.update.IncompatibleServerScreen].
 *
 * Server responses also carry ``latest_agent_version`` (central_server hop
 * only) — recorded here so the "agent outdated" banner can compare without
 * a separate round trip.
 */
class VersionGateRepository(
    private val centralApi: CentralApi,
) {
    private val _state = MutableStateFlow<ServerCompatibility>(ServerCompatibility.Unknown)
    val state: StateFlow<ServerCompatibility> = _state.asStateFlow()

    suspend fun probe(): ServerCompatibility {
        val outcome = try {
            classify(centralApi.getVersion())
        } catch (t: Throwable) {
            ServerCompatibility.Unknown
        }
        _state.value = outcome
        return outcome
    }

    fun reset() {
        _state.value = ServerCompatibility.Unknown
    }

    companion object {
        const val PLAY_DEEP_LINK_PRIMARY =
            "market://details?id=com.mobaide.app&referrer=utm_source%3Dversion_gate"
        const val PLAY_DEEP_LINK_FALLBACK =
            "https://play.google.com/store/apps/details?id=com.mobaide.app"

        internal fun classify(info: VersionInfoResponse): ServerCompatibility {
            val app = BuildConfig.APP_PROTOCOL_VERSION
            val supported = info.protocol_versions.toSet()
            val acceptable = app >= info.min_client_version && app in supported
            return if (acceptable) {
                ServerCompatibility.Compatible(
                    serverVersion = info.server_version,
                    protocolRange = info.protocol_versions,
                    latestAgentVersion = info.latest_agent_version,
                )
            } else {
                ServerCompatibility.Incompatible(
                    reason = buildReason(app, info),
                    minClientVersion = info.min_client_version,
                    playStoreUrl = PLAY_DEEP_LINK_PRIMARY,
                    playStoreFallbackUrl = PLAY_DEEP_LINK_FALLBACK,
                )
            }
        }

        private fun buildReason(appVersion: Int, info: VersionInfoResponse): String =
            "Server requires protocol v${info.min_client_version}+, this app speaks v$appVersion. " +
                "Server protocols: ${info.protocol_versions}."
    }
}

sealed interface ServerCompatibility {
    object Unknown : ServerCompatibility

    data class Compatible(
        val serverVersion: String,
        val protocolRange: List<Int>,
        val latestAgentVersion: String?,
    ) : ServerCompatibility

    data class Incompatible(
        val reason: String,
        val minClientVersion: Int,
        val playStoreUrl: String,
        val playStoreFallbackUrl: String,
    ) : ServerCompatibility
}
