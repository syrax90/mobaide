package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.VoiceHostInfo
import com.mobaide.app.data.network.dto.VoiceModelInfo
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The repository collects the bus on `Dispatchers.Default`, so the tests wait
 * on wall time for its subscription and for the status it writes.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class VoiceRepositoryTest {

    private val restApi = mockk<RestApi>()
    private val eventFlow = MutableSharedFlow<AgentEvent>(extraBufferCapacity = 64)
    private val bus = mockk<AgentEventBus>().also { every { it.events } returns eventFlow }
    private val sessionManager = mockk<SessionManager>().also {
        every { it.authState } returns
            MutableStateFlow<SessionManager.AuthState>(SessionManager.AuthState.FullyConnected)
    }

    private fun status(state: String) = VoiceStatusResponse(
        state = state,
        enabled = true,
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

    private suspend fun <T> onWallTime(block: suspend () -> T): T =
        withContext(Dispatchers.Default) { withTimeout(2_000) { block() } }

    @Test
    fun refresh_writesItsAnswer() = runTest {
        val downloading = status(VoiceStatusResponse.STATE_DOWNLOADING)
        coEvery { restApi.getVoiceStatus() } returns downloading
        val repo = VoiceRepository(restApi, bus, sessionManager)

        assertEquals(downloading, repo.refresh())

        assertEquals(downloading, repo.status.value)
    }

    @Test
    fun refresh_neverOverwritesAVoiceChangedThatArrivedWhileItWasInFlight() = runTest {
        val older = status(VoiceStatusResponse.STATE_DOWNLOADING)
        val pushed = status(VoiceStatusResponse.STATE_READY)
        val answer = CompletableDeferred<VoiceStatusResponse>()
        var asked = false
        coEvery { restApi.getVoiceStatus() } coAnswers {
            asked = true
            answer.await()
        }
        val repo = VoiceRepository(restApi, bus, sessionManager)
        onWallTime { eventFlow.subscriptionCount.first { it > 0 } }
        val refreshing = async { repo.refresh() }
        runCurrent()
        assertTrue(asked)

        eventFlow.emit(AgentEvent.VoiceChanged(pushed))
        onWallTime { repo.status.first { it == pushed } }
        answer.complete(older)

        assertEquals(older, refreshing.await())
        assertEquals(pushed, repo.status.value)
    }
}
