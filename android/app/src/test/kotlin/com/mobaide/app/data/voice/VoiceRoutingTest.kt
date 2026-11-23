package com.mobaide.app.data.voice

import com.mobaide.app.data.network.dto.CloudVoiceLimits
import com.mobaide.app.data.network.dto.CloudVoiceStatus
import com.mobaide.app.data.network.dto.CloudVoiceUsage
import com.mobaide.app.data.network.dto.VoiceHostInfo
import com.mobaide.app.data.network.dto.VoiceModelInfo
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Which engine transcribes an agent's dictations and how the session mic
 * shows it.
 */
class VoiceRoutingTest {

    private val agent = "a1b2c3d4e5f6"

    private fun cloud(state: String, engines: Map<String, String> = emptyMap()) = CloudVoiceStatus(
        state = state,
        limits = CloudVoiceLimits(sample_rate = 16000, max_seconds = 120, max_chunk_bytes = 65536),
        usage = CloudVoiceUsage(used_seconds = 0, limit_seconds = 1800, resets_at = "2026-11-23T00:00:00Z"),
        engines = engines,
    )

    private fun local(enabled: Boolean) = VoiceStatusResponse(
        state = if (enabled) VoiceStatusResponse.STATE_READY else VoiceStatusResponse.STATE_NOT_INSTALLED,
        enabled = enabled,
        model = VoiceModelInfo(
            id = "parakeet",
            name = "Parakeet",
            languages = listOf("en"),
            download_bytes = 670_000_000,
            disk_bytes = 700_000_000,
            ram_bytes = 1_200_000_000,
        ),
        host = VoiceHostInfo(recommendation = "ok"),
    )

    @Test
    fun route_staysOnTheComputer_whileCloudIsNotOffered_whateverWasChosen() {
        val chosen = mapOf(agent to CloudVoiceStatus.ENGINE_CLOUD)
        val notOffered = listOf(
            null,
            cloud(CloudVoiceStatus.STATE_OFF, chosen),
            cloud("some_future_state", chosen),
        )
        for (status in notOffered) {
            assertEquals(status?.state, VoiceRoute.Local, VoiceRouting.route(status, agent, null))
            assertEquals(status?.state, VoiceRoute.Local, VoiceRouting.route(status, agent, local(enabled = false)))
        }
        // Direct-connection mode: no agent of the account to choose for.
        assertEquals(VoiceRoute.Local, VoiceRouting.route(cloud(CloudVoiceStatus.STATE_READY, chosen), null, null))
    }

    @Test
    fun route_followsTheStoredChoice_overTheAgentsOwnVoice() {
        val expected = mapOf(
            CloudVoiceStatus.ENGINE_CLOUD to VoiceRoute.Cloud,
            CloudVoiceStatus.ENGINE_LOCAL to VoiceRoute.Local,
        )
        for ((engine, route) in expected) {
            val status = cloud(CloudVoiceStatus.STATE_READY, mapOf(agent to engine))
            assertEquals(engine, route, VoiceRouting.route(status, agent, local(enabled = true)))
            assertEquals(engine, route, VoiceRouting.route(status, agent, local(enabled = false)))
            assertEquals(engine, route, VoiceRouting.route(status, agent, null))
        }
    }

    @Test
    fun route_keepsACloudChoice_whileCloudCannotRecord() {
        for (state in listOf(CloudVoiceStatus.STATE_UNAVAILABLE, CloudVoiceStatus.STATE_LIMIT_REACHED)) {
            val status = cloud(state, mapOf(agent to CloudVoiceStatus.ENGINE_CLOUD))
            assertEquals(state, VoiceRoute.Cloud, VoiceRouting.route(status, agent, local(enabled = true)))
        }
    }

    @Test
    fun route_withoutAChoice_keepsEnabledOnComputerVoice_andAsksOtherwise() {
        val ready = cloud(CloudVoiceStatus.STATE_READY)
        assertEquals(VoiceRoute.Local, VoiceRouting.route(ready, agent, local(enabled = true)))
        assertEquals(VoiceRoute.Choose, VoiceRouting.route(ready, agent, local(enabled = false)))
        assertEquals(VoiceRoute.Choose, VoiceRouting.route(ready, agent, null))
    }

    @Test
    fun route_readsAnotherAgentsChoice_orAnUnknownEngine_asNoChoice() {
        val others = cloud(CloudVoiceStatus.STATE_READY, mapOf("other" to CloudVoiceStatus.ENGINE_CLOUD))
        val unknown = cloud(CloudVoiceStatus.STATE_READY, mapOf(agent to "remote"))
        assertEquals(VoiceRoute.Choose, VoiceRouting.route(others, agent, null))
        assertEquals(VoiceRoute.Choose, VoiceRouting.route(unknown, agent, null))
        assertEquals(VoiceRoute.Local, VoiceRouting.route(unknown, agent, local(enabled = true)))
    }

    @Test
    fun micMode_onTheLocalRoute_isTheOnComputerMic() {
        val ready = cloud(CloudVoiceStatus.STATE_READY)
        for (localMode in VoiceMicMode.entries) {
            assertEquals(localMode, VoiceRouting.micMode(VoiceRoute.Local, ready, localMode, hiddenByUser = false))
        }
    }

    @Test
    fun micMode_onTheCloudRoute_recordsOnlyWhenReady() {
        val expected = mapOf(
            CloudVoiceStatus.STATE_READY to VoiceMicMode.Ready,
            CloudVoiceStatus.STATE_UNAVAILABLE to VoiceMicMode.Unavailable,
            CloudVoiceStatus.STATE_LIMIT_REACHED to VoiceMicMode.Unavailable,
        )
        for ((state, mode) in expected) {
            // Whatever the on-computer mic would be.
            for (localMode in listOf(VoiceMicMode.Hidden, VoiceMicMode.Failed)) {
                assertEquals(
                    state,
                    mode,
                    VoiceRouting.micMode(VoiceRoute.Cloud, cloud(state), localMode, hiddenByUser = false),
                )
            }
        }
    }

    @Test
    fun micMode_withoutAChoice_opensTheChooser_andHidingHidesEveryRoute() {
        val ready = cloud(CloudVoiceStatus.STATE_READY)
        assertEquals(
            VoiceMicMode.Off,
            VoiceRouting.micMode(VoiceRoute.Choose, ready, VoiceMicMode.Hidden, hiddenByUser = false),
        )
        for (route in listOf(VoiceRoute.Cloud, VoiceRoute.Choose)) {
            assertEquals(
                route.name,
                VoiceMicMode.Hidden,
                VoiceRouting.micMode(route, ready, VoiceMicMode.Ready, hiddenByUser = true),
            )
        }
    }
}
