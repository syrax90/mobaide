package com.mobaide.app.data.analytics

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.MeResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneOffset

/**
 * Unit tests for [AgentPairedConversion]: the once-per-account rule, the
 * armed-window gate on the reconcile path, and the disabled-sink
 * short-circuit. The conversion state store is a map-backed mock so outcomes
 * can be asserted directly.
 */
class AgentPairedConversionTest {

    private class RecordingSink(override var isEnabled: Boolean = true) : AnalyticsSink {
        val events = mutableListOf<Pair<String, Map<String, String>>>()
        override fun logEvent(name: String, params: Map<String, String>) {
            events += name to params
        }
    }

    private class Fixture(userId: String? = "u1", now: Long = T0) {
        var now: Long = now
        val sink = RecordingSink()
        val outcomes = mutableMapOf<String, String>()
        var armedAt: Long? = null
        val api = mockk<CentralApi>()
        val identity = mockk<UserIdentityStore>()
        val prefs = mockk<ConversionStateStore>()
        val conversion: AgentPairedConversion

        init {
            every { identity.getUserId() } returns userId
            every { identity.getUserAccessToken() } returns "tok"
            coEvery { prefs.getOutcome(any()) } answers { outcomes[firstArg()] }
            coEvery { prefs.setOutcome(any(), any()) } answers {
                outcomes[firstArg()] = secondArg()
                Unit
            }
            coEvery { prefs.armIfUnset(any()) } answers {
                armedAt ?: firstArg<Long>().also { armedAt = it }
            }
            conversion = AgentPairedConversion(sink, api, identity, prefs) { this.now }
        }

        fun serverSays(firstPairedAt: String?, userId: String = "u1") {
            coEvery { api.getMe("tok") } returns MeResponse(
                user_id = userId,
                email = "u@x.com",
                role = "viewer",
                created_at = iso(T0 - 86_400_000L),
                first_paired_at = firstPairedAt,
            )
        }
    }

    // ---- live event ---------------------------------------------------------

    @Test
    fun liveEvent_logsOnce_andPersistsLogged() = runTest {
        val f = Fixture()

        f.conversion.onFirstAgentPairedEvent()
        f.conversion.onFirstAgentPairedEvent()

        assertEquals(listOf("agent_paired" to mapOf("source" to "live")), f.sink.events)
        assertEquals("logged", f.outcomes["u1"])
    }

    @Test
    fun liveEvent_sinkDisabled_logsNothing_persistsNothing() = runTest {
        val f = Fixture()
        f.sink.isEnabled = false

        f.conversion.onFirstAgentPairedEvent()

        assertTrue(f.sink.events.isEmpty())
        assertTrue(f.outcomes.isEmpty())
    }

    @Test
    fun liveEvent_noSignedInUser_logsNothing() = runTest {
        val f = Fixture(userId = null)

        f.conversion.onFirstAgentPairedEvent()

        assertTrue(f.sink.events.isEmpty())
        assertTrue(f.outcomes.isEmpty())
    }

    // ---- reconcile ----------------------------------------------------------

    @Test
    fun reconcile_sinkDisabled_neverCallsTheServer() = runTest {
        val f = Fixture()
        f.sink.isEnabled = false

        f.conversion.reconcile()

        coVerify(exactly = 0) { f.api.getMe(any()) }
        assertNull(f.armedAt)
        assertTrue(f.outcomes.isEmpty())
    }

    @Test
    fun reconcile_noSignedInUser_neverCallsTheServer() = runTest {
        val f = Fixture(userId = null)

        f.conversion.reconcile()

        coVerify(exactly = 0) { f.api.getMe(any()) }
    }

    @Test
    fun reconcile_storedOutcome_skipsTheServerCall() = runTest {
        val f = Fixture()
        f.outcomes["u1"] = "logged"

        f.conversion.reconcile()

        coVerify(exactly = 0) { f.api.getMe(any()) }
        assertTrue(f.sink.events.isEmpty())
    }

    @Test
    fun reconcile_unpairedAccount_persistsNothing_soTheNextConnectRetries() = runTest {
        val f = Fixture()
        f.serverSays(firstPairedAt = null)

        f.conversion.reconcile()

        assertTrue(f.sink.events.isEmpty())
        assertTrue(f.outcomes.isEmpty())
        assertEquals(T0, f.armedAt)
    }

    @Test
    fun reconcile_pairedAfterArming_logsAndPersistsLogged() = runTest {
        val f = Fixture()
        f.armedAt = T0
        f.now = T0 + 3 * 86_400_000L
        f.serverSays(firstPairedAt = iso(T0 + 2 * 86_400_000L))

        f.conversion.reconcile()

        assertEquals(listOf("agent_paired" to mapOf("source" to "reconcile")), f.sink.events)
        assertEquals("logged", f.outcomes["u1"])
    }

    @Test
    fun reconcile_pairedBeforeArming_persistsSkip_withoutLogging() = runTest {
        val f = Fixture()
        f.armedAt = T0
        f.serverSays(firstPairedAt = iso(T0 - 2 * 3_600_000L))

        f.conversion.reconcile()

        assertTrue(f.sink.events.isEmpty())
        assertEquals("skipped_predates_install", f.outcomes["u1"])
    }

    @Test
    fun reconcile_pairedInsideTheSkewTolerance_logs() = runTest {
        val f = Fixture()
        f.armedAt = T0
        f.serverSays(firstPairedAt = iso(T0 - 30 * 60_000L))

        f.conversion.reconcile()

        assertEquals(1, f.sink.events.size)
        assertEquals("logged", f.outcomes["u1"])
    }

    @Test
    fun reconcile_acceptsAServerStampWithoutFractionalSeconds() = runTest {
        val f = Fixture()
        f.armedAt = T0
        f.serverSays(firstPairedAt = "2026-09-03T15:07:12+00:00")
        f.now = Instant.parse("2026-09-03T16:00:00Z").toEpochMilli()
        f.armedAt = Instant.parse("2026-09-03T15:00:00Z").toEpochMilli()

        f.conversion.reconcile()

        assertEquals("logged", f.outcomes["u1"])
    }

    @Test
    fun reconcile_serverFailure_persistsNothing() = runTest {
        val f = Fixture()
        coEvery { f.api.getMe("tok") } throws RuntimeException("offline")

        f.conversion.reconcile()

        assertTrue(f.sink.events.isEmpty())
        assertTrue(f.outcomes.isEmpty())
    }

    @Test
    fun reconcile_unparseableStamp_persistsNothing() = runTest {
        val f = Fixture()
        f.serverSays(firstPairedAt = "yesterday")

        f.conversion.reconcile()

        assertTrue(f.sink.events.isEmpty())
        assertTrue(f.outcomes.isEmpty())
    }

    @Test
    fun reconcile_identityChangedMidFlight_persistsNothing() = runTest {
        val f = Fixture()
        f.serverSays(firstPairedAt = iso(T0 + 60_000L), userId = "someone-else")

        f.conversion.reconcile()

        assertTrue(f.sink.events.isEmpty())
        assertTrue(f.outcomes.isEmpty())
    }

    @Test
    fun reconcile_alreadyLoggedLive_doesNotLogAgain() = runTest {
        val f = Fixture()
        f.serverSays(firstPairedAt = iso(T0 + 60_000L))

        f.conversion.onFirstAgentPairedEvent()
        f.conversion.reconcile()

        assertEquals(1, f.sink.events.size)
    }

    @Test
    fun liveAndReconcileConcurrently_logExactlyOnce() = runTest {
        val f = Fixture()
        f.armedAt = T0
        f.serverSays(firstPairedAt = iso(T0 + 60_000L))

        listOf(
            async { f.conversion.onFirstAgentPairedEvent() },
            async { f.conversion.reconcile() },
            async { f.conversion.onFirstAgentPairedEvent() },
        ).awaitAll()

        assertEquals(1, f.sink.events.size)
        assertEquals("logged", f.outcomes["u1"])
    }

    // ---- arming -------------------------------------------------------------

    @Test
    fun armIfNeeded_recordsTheLaunchTime_once() = runTest {
        val f = Fixture()

        f.conversion.armIfNeeded()
        f.now = T0 + 3_600_000L
        f.conversion.armIfNeeded()

        assertEquals(T0, f.armedAt)
    }

    @Test
    fun armIfNeeded_sinkDisabled_doesNotArm() = runTest {
        val f = Fixture()
        f.sink.isEnabled = false

        f.conversion.armIfNeeded()

        assertNull(f.armedAt)
    }

    @Test
    fun reconcile_pairingAfterLaunchButBeforeFirstConnect_logs() = runTest {
        val f = Fixture()
        f.conversion.armIfNeeded()
        f.serverSays(firstPairedAt = iso(T0 + 2 * 3_600_000L))
        f.now = T0 + 5 * 3_600_000L

        f.conversion.reconcile()

        assertEquals(listOf("agent_paired" to mapOf("source" to "reconcile")), f.sink.events)
        assertEquals("logged", f.outcomes["u1"])
    }

    private companion object {
        val T0: Long = Instant.parse("2026-09-04T12:00:00Z").toEpochMilli()

        fun iso(millis: Long): String =
            Instant.ofEpochMilli(millis).atOffset(ZoneOffset.UTC).toString()
    }
}
