package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.HerdrOpenPaneResponse
import com.mobaide.app.data.network.dto.HerdrServer
import com.mobaide.app.data.network.dto.HerdrSnapshot
import com.mobaide.app.data.network.dto.VersionInfoResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.IOException

/**
 * [HerdrRepository]: the refusals the screens branch on, and whose servers the
 * dashboard shows.
 *
 * Robolectric so the error body is read by a real `org.json.JSONObject`. The
 * repository collects the bus on `Dispatchers.Default`, so the tests wait on
 * wall time for its subscription and for the state it writes, as in
 * `VoiceRepositoryTest`.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class HerdrRepositoryTest {

    private val restApi = mockk<RestApi>()
    private val eventFlow = MutableSharedFlow<AgentEvent>(extraBufferCapacity = 64)
    private val bus = mockk<AgentEventBus>().also { every { it.events } returns eventFlow }
    private var agentId: String? = "agent-1"
    private val tokenStore = mockk<TokenStore>().also { every { it.getAgentId() } answers { agentId } }

    private fun snapshot(serverId: String) = HerdrSnapshot(listOf(HerdrServer(server_id = serverId)))

    private fun version(capabilities: String) = VersionInfoResponse(
        name = "mobaide_agent",
        server_version = "1.4.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = Json.parseToJsonElement(capabilities) as JsonObject,
    )

    private suspend fun <T> onWallTime(block: suspend () -> T): T =
        withContext(Dispatchers.Default) { withTimeout(2_000) { block() } }

    /** The repository collects the bus twice: for `herdr.changed` and for `sessions.changed`. */
    private suspend fun subscribedRepository(events: MutableSharedFlow<AgentEvent> = eventFlow): HerdrRepository {
        val repo = HerdrRepository(restApi, bus, tokenStore)
        onWallTime { events.subscriptionCount.first { it >= 2 } }
        return repo
    }

    private suspend fun refusalOf(failure: Exception): HerdrException {
        coEvery { restApi.openHerdrPane(any(), any()) } throws failure
        try {
            HerdrRepository(restApi, bus, tokenStore).openPane("tui:default", "w1:p1")
        } catch (e: HerdrException) {
            return e
        }
        fail("expected a HerdrException")
        throw IllegalStateException()
    }

    @Test
    fun codedRefusal_liftsTheCodeAndTheAgentsOwnWords() = runTest {
        val refusal = refusalOf(
            ApiException(404, """{"detail":{"code":"pane_not_found","message":"This pane is closed."}}"""),
        )

        assertEquals("pane_not_found", refusal.code)
        assertEquals("This pane is closed.", refusal.message)
        assertTrue(refusal.isGone)
    }

    @Test
    fun stoppedServer_isGone_andAnyOtherCodeIsNot() = runTest {
        val gone = refusalOf(ApiException(404, """{"detail":{"code":"server_not_found","message":"x"}}"""))
        val refused = refusalOf(ApiException(409, """{"detail":{"code":"herdr_refused","message":"busy"}}"""))

        assertTrue(gone.isGone)
        assertFalse(refused.isGone)
        assertEquals("busy", refused.message)
    }

    @Test
    fun refusalWithoutACodedDetail_isUncoded_andStillHasAMessage() = runTest {
        val relay = refusalOf(ApiException(503, """{"detail":"Agent offline"}"""))
        val html = refusalOf(ApiException(502, "<html>Bad gateway</html>"))
        val validation = refusalOf(ApiException(422, """{"detail":[{"loc":["pane_id"],"msg":"too long"}]}"""))

        assertNull(relay.code)
        assertEquals("Agent offline", relay.message)
        for (refusal in listOf(html, validation)) {
            assertNull(refusal.code)
            assertFalse(refusal.isGone)
            assertTrue(refusal.message!!.isNotBlank())
        }
    }

    @Test
    fun ioFailure_isAHerdrException_notACrash() = runTest {
        val refusal = refusalOf(IOException("connection reset"))

        assertNull(refusal.code)
        assertFalse(refusal.isGone)
    }

    @Test
    fun openPane_answersTheSessionId() = runTest {
        coEvery { restApi.openHerdrPane("core:default", "box/t3") } returns HerdrOpenPaneResponse("herdr-0123")

        assertEquals("herdr-0123", HerdrRepository(restApi, bus, tokenStore).openPane("core:default", "box/t3"))
    }

    @Test
    fun refresh_withoutTheCapability_asksForNothing() = runTest {
        // An agent older than the dashboard has no `/api/herdr`.
        coEvery { restApi.getAgentVersion() } returns version("""{"voice":{"dictation":true}}""")
        val repo = HerdrRepository(restApi, bus, tokenStore)

        repo.refresh()

        assertNull(repo.snapshot.value)
        coVerify(exactly = 0) { restApi.getHerdr() }
    }

    @Test
    fun refresh_seedsTheSnapshot_andHerdrChangedReplacesIt() = runTest {
        coEvery { restApi.getAgentVersion() } returns version("""{"herdr":{"dashboard":true}}""")
        coEvery { restApi.getHerdr() } returns snapshot("tui:default")
        val repo = subscribedRepository()

        repo.refresh()
        assertEquals(snapshot("tui:default"), repo.snapshot.value)

        eventFlow.emit(AgentEvent.HerdrChanged(snapshot("core:default")))
        onWallTime { repo.snapshot.first { it == snapshot("core:default") } }
    }

    @Test
    fun refresh_whoseReadIsOvertakenByAPush_keepsThePushedState() = runTest {
        // A pane closed during the read leaves on the push. The older REST
        // answer must not bring it back.
        coEvery { restApi.getAgentVersion() } returns version("""{"herdr":{"dashboard":true}}""")
        val readStarted = CompletableDeferred<Unit>()
        val answer = CompletableDeferred<HerdrSnapshot>()
        coEvery { restApi.getHerdr() } coAnswers {
            readStarted.complete(Unit)
            answer.await()
        }
        val repo = subscribedRepository()

        val refreshing = launch { repo.refresh() }
        readStarted.await()
        eventFlow.emit(AgentEvent.HerdrChanged(snapshot("core:default")))
        onWallTime { repo.snapshot.first { it == snapshot("core:default") } }
        answer.complete(snapshot("tui:default"))
        refreshing.join()

        assertEquals(snapshot("core:default"), repo.snapshot.value)
    }

    @Test
    fun refresh_forAnotherAgent_dropsThePreviousAgentsServers() = runTest {
        coEvery { restApi.getAgentVersion() } returns version("""{"herdr":{"dashboard":true}}""")
        coEvery { restApi.getHerdr() } returns snapshot("tui:default")
        val repo = HerdrRepository(restApi, bus, tokenStore)
        repo.refresh()
        assertEquals(snapshot("tui:default"), repo.snapshot.value)

        // The next agent has no dashboard: the servers of the first one must
        // not stay on screen under its name.
        agentId = "agent-2"
        coEvery { restApi.getAgentVersion() } returns version("{}")
        repo.refresh()

        assertNull(repo.snapshot.value)
    }

    @Test
    fun refresh_keepsTheServersWhenTheReadFails_andDropsThemWhenTheAgentHasNoDashboardAnyMore() = runTest {
        coEvery { restApi.getAgentVersion() } returns version("""{"herdr":{"dashboard":true}}""")
        coEvery { restApi.getHerdr() } returns snapshot("tui:default")
        val repo = HerdrRepository(restApi, bus, tokenStore)
        repo.refresh()
        assertEquals(snapshot("tui:default"), repo.snapshot.value)

        // A read that failed says nothing about the agent: a bad connection
        // must not empty the dashboard.
        coEvery { restApi.getAgentVersion() } throws IOException("timeout")
        repo.refresh()
        assertEquals(snapshot("tui:default"), repo.snapshot.value)

        // The same agent answers, and has no dashboard any more.
        coEvery { restApi.getAgentVersion() } returns version("""{"voice":{"dictation":true}}""")
        repo.refresh()
        assertNull(repo.snapshot.value)
    }

    @Test
    fun sessionsChanged_whileServersAreHeld_asksTheAgentAgain() = runTest {
        coEvery { restApi.getAgentVersion() } returns version("""{"herdr":{"dashboard":true}}""")
        coEvery { restApi.getHerdr() } returns snapshot("tui:default")
        val repo = subscribedRepository()
        repo.refresh()
        assertEquals(snapshot("tui:default"), repo.snapshot.value)

        // The agent came back without the dashboard: it sends its session list
        // on connect, and no `herdr.changed` that would correct the screen.
        coEvery { restApi.getAgentVersion() } returns version("{}")
        eventFlow.emit(AgentEvent.SessionsChanged(emptyList()))

        onWallTime { repo.snapshot.first { it == null } }
    }

    @Test
    fun sessionsChanged_readsOnlyTheVersion_andKeepsThePushedState() = runTest {
        // The state arrives as `herdr.changed`. A REST copy read by the check
        // could be older than the pushed one and replace it.
        coEvery { restApi.getAgentVersion() } returns version("""{"herdr":{"dashboard":true}}""")
        coEvery { restApi.getHerdr() } returns snapshot("tui:default")
        val repo = subscribedRepository()
        repo.refresh()
        eventFlow.emit(AgentEvent.HerdrChanged(snapshot("core:default")))
        onWallTime { repo.snapshot.first { it == snapshot("core:default") } }
        val versionReads = Channel<Unit>(Channel.UNLIMITED)
        coEvery { restApi.getAgentVersion() } coAnswers {
            versionReads.send(Unit)
            version("""{"herdr":{"dashboard":true}}""")
        }

        eventFlow.emit(AgentEvent.SessionsChanged(emptyList()))
        onWallTime { versionReads.receive() }
        // The checks run one at a time: the read of a second check starts
        // when the first check ran to its end.
        eventFlow.emit(AgentEvent.SessionsChanged(emptyList()))
        onWallTime { versionReads.receive() }

        coVerify(exactly = 1) { restApi.getHerdr() }
        assertEquals(snapshot("core:default"), repo.snapshot.value)
    }

    @Test
    fun sessionsChanged_whileNoServersAreHeld_asksTheAgentNothing() = runTest {
        coEvery { restApi.getAgentVersion() } returns version("""{"herdr":{"dashboard":true}}""")
        coEvery { restApi.getHerdr() } returns snapshot("tui:default")
        // The collector is conflated: an emit returns when the event is taken
        // off the bus, not when it was handled. Nothing observable follows a
        // check that does nothing, so the test gives the collector a wall-time
        // window to run before it counts the reads.
        val handoff = MutableSharedFlow<AgentEvent>()
        every { bus.events } returns handoff
        val repo = subscribedRepository(handoff)

        onWallTime {
            handoff.emit(AgentEvent.SessionsChanged(emptyList()))
            handoff.emit(AgentEvent.SessionsChanged(emptyList()))
            delay(300)
        }

        // An agent without the dashboard gets no version read per session event.
        coVerify(exactly = 0) { restApi.getAgentVersion() }
        assertNull(repo.snapshot.value)
    }
}
