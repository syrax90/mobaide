package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.VoiceHostInfo
import com.mobaide.app.data.network.dto.VoiceModelInfo
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import com.mobaide.app.data.voice.VoiceApiException
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
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
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
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

    @Test
    fun setCleanup_showsTheChoiceAtOnce_thenTheAgentsAnswer() = runTest {
        val off = status(VoiceStatusResponse.STATE_READY)
        val saved = off.copy(cleanup_enabled = true, speed_factor = 0.1)
        val answer = CompletableDeferred<VoiceStatusResponse>()
        coEvery { restApi.getVoiceStatus() } returns off
        coEvery { restApi.setVoiceCleanupSettings(true) } coAnswers { answer.await() }
        val repo = VoiceRepository(restApi, bus, sessionManager)
        repo.refresh()

        val saving = async { repo.setCleanup(true) }
        runCurrent()
        assertTrue(repo.status.value!!.cleanup_enabled)
        answer.complete(saved)

        assertEquals(saved, saving.await())
        assertEquals(saved, repo.status.value)
    }

    @Test
    fun setCleanup_refused_restoresTheOldValue() = runTest {
        val off = status(VoiceStatusResponse.STATE_READY)
        coEvery { restApi.getVoiceStatus() } returns off
        coEvery { restApi.setVoiceCleanupSettings(true) } throws ApiException(503, "busy")
        val repo = VoiceRepository(restApi, bus, sessionManager)
        repo.refresh()

        try {
            repo.setCleanup(true)
            fail("expected a VoiceApiException")
        } catch (e: VoiceApiException) {
            assertEquals(503, e.status)
        }

        assertFalse(repo.status.value!!.cleanup_enabled)
    }

    @Test
    fun setCleanup_neverOverwritesAVoiceChangedThatArrivedWhileItWasInFlight() = runTest {
        val downloading = status(VoiceStatusResponse.STATE_DOWNLOADING)
        val pushed = status(VoiceStatusResponse.STATE_READY).copy(cleanup_enabled = true)
        val answer = CompletableDeferred<VoiceStatusResponse>()
        coEvery { restApi.getVoiceStatus() } returns downloading
        coEvery { restApi.setVoiceCleanupSettings(true) } coAnswers { answer.await() }
        val repo = VoiceRepository(restApi, bus, sessionManager)
        onWallTime { eventFlow.subscriptionCount.first { it > 0 } }
        repo.refresh()
        val saving = async { repo.setCleanup(true) }
        runCurrent()

        eventFlow.emit(AgentEvent.VoiceChanged(pushed))
        onWallTime { repo.status.first { it == pushed } }
        answer.complete(downloading.copy(cleanup_enabled = true))
        saving.await()

        assertEquals(pushed, repo.status.value)
    }

    @Test
    fun cleanup_sendsTheSessionKindWithTheTranscript() = runTest {
        val client = mockk<OkHttpClient>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns "https://api.example.com"
        val captured = slot<Request>()
        val call = mockk<Call>()
        every { client.newCall(capture(captured)) } returns call
        every { call.enqueue(any()) } answers {
            val response = Response.Builder()
                .request(captured.captured)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body("""{"text":"Run the tests with --watch."}""".toResponseBody("application/json".toMediaType()))
                .build()
            firstArg<Callback>().onResponse(call, response)
        }
        val repo = VoiceRepository(RestApi(client, tokenStore), bus, sessionManager)

        val cleaned = repo.cleanup("uh run the tests dash dash watch", "codex")

        assertEquals("Run the tests with --watch.", cleaned)
        assertEquals("/api/voice/cleanup", captured.captured.url.encodedPath)
        val body = Buffer().also { captured.captured.body!!.writeTo(it) }.readUtf8()
        assertEquals(
            JsonObject(
                mapOf(
                    "text" to JsonPrimitive("uh run the tests dash dash watch"),
                    "kind" to JsonPrimitive("codex"),
                ),
            ),
            Json.parseToJsonElement(body),
        )
    }
}
