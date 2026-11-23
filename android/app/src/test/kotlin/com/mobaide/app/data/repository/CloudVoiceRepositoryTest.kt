package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.CloudVoiceLimits
import com.mobaide.app.data.network.dto.CloudVoiceStatus
import com.mobaide.app.data.network.dto.CloudVoiceUsage
import com.mobaide.app.data.network.dto.VoiceDictationResponse
import com.mobaide.app.data.voice.VoiceApiException
import com.mobaide.app.data.voice.VoiceDictationOpened
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Test

/**
 * [CloudVoiceRepository]: "not offered" for an older server and for no
 * account, the engine choice shown at once and taken back on a refusal, and
 * which answer wins when a refresh, a save and an account change cross.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class CloudVoiceRepositoryTest {

    private val centralApi = mockk<CentralApi>()
    private var userId: String? = "user-1"
    private var token: String? = "token-1"
    private val identity = mockk<UserIdentityStore>().also {
        every { it.getUserId() } answers { userId }
        every { it.getUserAccessToken() } answers { token }
    }
    private val agent = "a1b2c3d4e5f6"

    private fun repository() = CloudVoiceRepository(centralApi, identity)

    private fun status(
        state: String = CloudVoiceStatus.STATE_READY,
        engines: Map<String, String> = emptyMap(),
        usedSeconds: Int = 0,
    ) = CloudVoiceStatus(
        state = state,
        limits = CloudVoiceLimits(sample_rate = 16000, max_seconds = 120, max_chunk_bytes = 65536),
        usage = CloudVoiceUsage(used_seconds = usedSeconds, limit_seconds = 1800, resets_at = "2026-11-23T00:00:00Z"),
        engines = engines,
    )

    private fun refusal(status: Int, code: String) =
        ApiException(status, """{"detail":{"code":"$code","message":"refused"}}""")

    @Test
    fun refresh_onAServerWithoutTheRoute_readsAsNotOffered() = runTest {
        coEvery { centralApi.getCloudVoice("token-1") } throws ApiException(404, """{"detail":"Not Found"}""")
        val repo = repository()

        repo.refresh()

        assertEquals(CloudVoiceStatus.NOT_OFFERED, repo.status.value)
    }

    @Test
    fun refresh_withoutAnAccount_isNotOffered_andAsksNoServer() = runTest {
        token = null
        val repo = repository()

        repo.refresh()

        assertEquals(CloudVoiceStatus.NOT_OFFERED, repo.status.value)
        coVerify(exactly = 0) { centralApi.getCloudVoice(any()) }
    }

    @Test
    fun refresh_thatFails_keepsTheLastAnswer_andReachesTheCaller() = runTest {
        val ready = status()
        coEvery { centralApi.getCloudVoice("token-1") } returns ready
        val repo = repository()
        repo.refresh()
        coEvery { centralApi.getCloudVoice("token-1") } throws ApiException(502, "")

        try {
            repo.refresh()
            fail("the failure must reach the caller")
        } catch (e: ApiException) {
            assertEquals(502, e.code)
        }

        assertEquals(ready, repo.status.value)
    }

    @Test
    fun refresh_withAMaxAge_keepsAnAnswerYoungerThanThat() = runTest {
        coEvery { centralApi.getCloudVoice("token-1") } returns status()
        val repo = repository()
        repo.refresh()

        repo.refresh(maxAgeMs = 60_000)
        coVerify(exactly = 1) { centralApi.getCloudVoice(any()) }

        repo.refresh()
        coVerify(exactly = 2) { centralApi.getCloudVoice(any()) }
    }

    @Test
    fun setEngine_showsTheChoiceAtOnce_thenTheServersAnswer() = runTest {
        val before = status(engines = mapOf("other" to CloudVoiceStatus.ENGINE_LOCAL))
        val saved = status(
            engines = mapOf("other" to CloudVoiceStatus.ENGINE_LOCAL, agent to CloudVoiceStatus.ENGINE_CLOUD),
            usedSeconds = 61,
        )
        val answer = CompletableDeferred<CloudVoiceStatus>()
        coEvery { centralApi.getCloudVoice("token-1") } returns before
        coEvery {
            centralApi.putCloudVoiceEngine("token-1", agent, CloudVoiceStatus.ENGINE_CLOUD)
        } coAnswers { answer.await() }
        val repo = repository()
        repo.refresh()

        val saving = async { repo.setEngine(agent, CloudVoiceStatus.ENGINE_CLOUD) }
        runCurrent()
        assertEquals(
            mapOf("other" to CloudVoiceStatus.ENGINE_LOCAL, agent to CloudVoiceStatus.ENGINE_CLOUD),
            repo.status.value!!.engines,
        )
        answer.complete(saved)
        saving.await()

        assertEquals(saved, repo.status.value)
    }

    @Test
    fun setEngine_refused_takesTheChoiceBack_andRaisesTheCode() = runTest {
        val before = status(engines = mapOf(agent to CloudVoiceStatus.ENGINE_LOCAL))
        coEvery { centralApi.getCloudVoice("token-1") } returns before
        coEvery {
            centralApi.putCloudVoiceEngine("token-1", agent, CloudVoiceStatus.ENGINE_CLOUD)
        } throws refusal(403, "cloud_disabled")
        val repo = repository()
        repo.refresh()

        try {
            repo.setEngine(agent, CloudVoiceStatus.ENGINE_CLOUD)
            fail("the refusal must reach the caller")
        } catch (e: VoiceApiException) {
            assertEquals(403, e.status)
            assertEquals("cloud_disabled", e.code)
        }

        assertEquals(before, repo.status.value)
    }

    @Test
    fun choiceBeingSaved_staysOnTop_ofARefreshThatStillSaysTheOldOne() = runTest {
        val old = status(engines = mapOf(agent to CloudVoiceStatus.ENGINE_LOCAL))
        val saved = status(engines = mapOf(agent to CloudVoiceStatus.ENGINE_CLOUD))
        val answer = CompletableDeferred<CloudVoiceStatus>()
        coEvery { centralApi.getCloudVoice("token-1") } returns old
        coEvery {
            centralApi.putCloudVoiceEngine("token-1", agent, CloudVoiceStatus.ENGINE_CLOUD)
        } coAnswers { answer.await() }
        val repo = repository()
        repo.refresh()
        val saving = async { repo.setEngine(agent, CloudVoiceStatus.ENGINE_CLOUD) }
        runCurrent()

        // A push from another device, or a screen, reads the status meanwhile.
        repo.refresh()

        assertEquals(CloudVoiceStatus.ENGINE_CLOUD, repo.status.value!!.engines[agent])
        answer.complete(saved)
        saving.await()
        assertEquals(saved, repo.status.value)
    }

    @Test
    fun refreshThatStartedBeforeASave_neverBringsTheOldChoiceBack() = runTest {
        val old = status(engines = mapOf(agent to CloudVoiceStatus.ENGINE_LOCAL))
        val saved = status(engines = mapOf(agent to CloudVoiceStatus.ENGINE_CLOUD))
        val slowRead = CompletableDeferred<CloudVoiceStatus>()
        coEvery { centralApi.getCloudVoice("token-1") } coAnswers { slowRead.await() }
        coEvery {
            centralApi.putCloudVoiceEngine("token-1", agent, CloudVoiceStatus.ENGINE_CLOUD)
        } returns saved
        val repo = repository()
        val refreshing = async { repo.refresh() }
        runCurrent()

        repo.setEngine(agent, CloudVoiceStatus.ENGINE_CLOUD)
        slowRead.complete(old)
        refreshing.await()

        assertEquals(saved, repo.status.value)
    }

    @Test
    fun clear_dropsTheAnswerOfARefreshStillInFlight() = runTest {
        val slowRead = CompletableDeferred<CloudVoiceStatus>()
        coEvery { centralApi.getCloudVoice("token-1") } coAnswers { slowRead.await() }
        val repo = repository()
        val refreshing = async { repo.refresh() }
        runCurrent()

        repo.clear()
        slowRead.complete(status(engines = mapOf(agent to CloudVoiceStatus.ENGINE_CLOUD)))
        refreshing.await()

        assertNull(repo.status.value)
    }

    @Test
    fun saveQueuedBeforeAnAccountChange_neverRunsForTheNextAccount() = runTest {
        val firstAnswer = CompletableDeferred<CloudVoiceStatus>()
        coEvery {
            centralApi.putCloudVoiceEngine(any(), agent, CloudVoiceStatus.ENGINE_CLOUD)
        } coAnswers { firstAnswer.await() }
        val repo = repository()
        val first = async { repo.setEngine(agent, CloudVoiceStatus.ENGINE_CLOUD) }
        runCurrent()
        val queued = async { repo.setEngine("0f9e8d7c6b5a", CloudVoiceStatus.ENGINE_CLOUD) }
        runCurrent()

        userId = "user-2"
        token = "token-2"
        repo.clear()
        firstAnswer.complete(status(engines = mapOf(agent to CloudVoiceStatus.ENGINE_CLOUD)))
        first.await()
        queued.await()

        // The first account's answer is not shown to the second, and the
        // queued save was not sent with the second account's token.
        assertNull(repo.status.value)
        coVerify(exactly = 1) { centralApi.putCloudVoiceEngine(any(), any(), any()) }
        coVerify(exactly = 0) { centralApi.putCloudVoiceEngine("token-2", any(), any()) }
    }

    @Test
    fun dictationApi_opensOnTheCentralServer_andRaisesItsCodedRefusals() = runTest {
        coEvery { centralApi.createCloudDictation("token-1") } returns VoiceDictationResponse(
            dictation_id = "d1",
            sample_rate = 16000,
            max_seconds = 120,
            max_chunk_bytes = 65536,
        )
        val api = repository().dictationApi()

        assertEquals(VoiceDictationOpened("d1", 16000, 120, 65536), api.create())

        coEvery { centralApi.createCloudDictation("token-1") } throws refusal(429, "cloud_limit_reached")
        try {
            api.create()
            fail("the refusal must reach the uploader")
        } catch (e: VoiceApiException) {
            assertEquals(429, e.status)
            assertEquals("cloud_limit_reached", e.code)
        }
    }
}
