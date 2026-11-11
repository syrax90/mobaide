package com.mobaide.app.data.voice

import com.mobaide.app.data.network.dto.VoiceCapability
import com.mobaide.app.data.network.dto.VoiceHostInfo
import com.mobaide.app.data.network.dto.VoiceModelInfo
import com.mobaide.app.data.network.dto.VoiceProgress
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The status → mic mapping and the progress percentage. */
class VoiceStatusViewTest {

    private val capability = VoiceCapability(sampleRate = 16000, maxSeconds = 120, maxChunkBytes = 65536)

    private fun status(
        state: String = VoiceStatusResponse.STATE_READY,
        repairing: Boolean = false,
        progress: VoiceProgress? = null,
    ) = VoiceStatusResponse(
        state = state,
        enabled = true,
        repairing = repairing,
        model = VoiceModelInfo(
            id = "parakeet",
            name = "Parakeet",
            languages = listOf("en"),
            download_bytes = 670_000_000,
            disk_bytes = 700_000_000,
            ram_bytes = 1_200_000_000,
        ),
        progress = progress,
        host = VoiceHostInfo(recommendation = "ok"),
    )

    @Test
    fun micMode_mapsEveryKnownState_andHidesTheRest() {
        val expected = mapOf(
            VoiceStatusResponse.STATE_NOT_INSTALLED to VoiceMicMode.Off,
            VoiceStatusResponse.STATE_QUEUED to VoiceMicMode.Preparing,
            VoiceStatusResponse.STATE_DOWNLOADING to VoiceMicMode.Preparing,
            VoiceStatusResponse.STATE_VERIFYING to VoiceMicMode.Preparing,
            VoiceStatusResponse.STATE_FAILED to VoiceMicMode.Failed,
            VoiceStatusResponse.STATE_READY to VoiceMicMode.Ready,
            VoiceStatusResponse.STATE_UNSUPPORTED to VoiceMicMode.Hidden,
            "some_future_state" to VoiceMicMode.Hidden,
        )
        for ((state, mode) in expected) {
            assertEquals(state, mode, VoiceStatusView.micMode(capability, status(state), hiddenByUser = false))
        }
    }

    @Test
    fun micMode_isHiddenWhenTheUserHidIt_orWithoutVoiceOrStatus() {
        for (state in listOf(VoiceStatusResponse.STATE_NOT_INSTALLED, VoiceStatusResponse.STATE_READY)) {
            assertEquals(VoiceMicMode.Hidden, VoiceStatusView.micMode(capability, status(state), hiddenByUser = true))
        }
        assertEquals(VoiceMicMode.Hidden, VoiceStatusView.micMode(null, status(), hiddenByUser = false))
        assertEquals(VoiceMicMode.Hidden, VoiceStatusView.micMode(capability, null, hiddenByUser = false))
    }

    @Test
    fun progressPercent_floors_capsAt100_andIsUnknownWithoutATotal() {
        assertEquals(99, VoiceStatusView.progressPercent(status(progress = VoiceProgress(999, 1000))))
        assertEquals(100, VoiceStatusView.progressPercent(status(progress = VoiceProgress(1200, 1000))))
        assertNull(VoiceStatusView.progressPercent(status(progress = VoiceProgress(0, 0))))
        assertNull(VoiceStatusView.progressPercent(status()))
    }

}
