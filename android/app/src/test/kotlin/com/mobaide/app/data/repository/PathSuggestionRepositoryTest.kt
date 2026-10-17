package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.fs.ListingHit
import com.mobaide.app.data.fs.ListingRequest
import com.mobaide.app.data.fs.PathCompletion
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.FsDirsResponse
import com.mobaide.app.data.network.dto.VersionInfoResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * [PathSuggestionRepository]: which failures are remembered as an empty
 * listing, the per-agent split of the cache, and the five-minute capability
 * probe. [RestApi] is mocked at the method level, as the other repository
 * tests do; the clock is a hand-driven `now`.
 */
class PathSuggestionRepositoryTest {

    private var now = 1_000_000L
    private var agentId: String? = "agent-1"
    private val restApi = mockk<RestApi>()
    private val tokenStore = mockk<TokenStore>().also {
        every { it.getAgentId() } answers { agentId }
        every { it.getAgentUrl() } returns null
    }
    private val repo = PathSuggestionRepository(restApi, tokenStore) { now }

    private fun version(capabilities: String) = VersionInfoResponse(
        name = "mobaide_agent",
        server_version = "1.4.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = Json.parseToJsonElement(capabilities) as JsonObject,
    )

    private fun query(text: String) = PathCompletion.split(text)!!

    @Test
    fun fetch_cachesTheListing_soTheNextKeystrokeIsAnsweredLocally() = runTest {
        val listing = FsDirsResponse("/srv", listOf("alpha"), truncated = false)
        coEvery { restApi.listDirs("/srv/", null) } returns listing

        assertTrue(repo.fetch(ListingRequest("/srv/", null)))

        assertEquals(ListingHit(listing, complete = true), repo.lookup(query("/srv/al")))
        assertNull(repo.nextRequest(query("/srv/al")))
    }

    @Test
    fun fetch_remembersAMissingDeniedOrRelativeDirectoryAsEmpty() = runTest {
        for ((dir, code) in listOf("/gone/" to 404, "/locked/" to 403, "/bad/" to 400)) {
            coEvery { restApi.listDirs(dir, null) } throws ApiException(code, "{}")

            assertTrue(dir, repo.fetch(ListingRequest(dir, null)))

            assertEquals(
                dir,
                ListingHit(FsDirsResponse(dir, emptyList(), truncated = false), complete = true),
                repo.lookup(query("${dir}x")),
            )
        }
    }

    @Test
    fun fetch_doesNotRememberABusyAgentOrANetworkFailure() = runTest {
        coEvery { restApi.listDirs("/busy/", null) } throws ApiException(503, "{}")
        coEvery { restApi.listDirs("/net/", null) } throws IOException("reset")

        assertFalse(repo.fetch(ListingRequest("/busy/", null)))
        assertFalse(repo.fetch(ListingRequest("/net/", null)))

        assertNull(repo.lookup(query("/busy/")))
        assertEquals(ListingRequest("/net/", null), repo.nextRequest(query("/net/")))
    }

    @Test
    fun listings_areKeptPerAgent() = runTest {
        coEvery { restApi.listDirs("/srv/", null) } returns FsDirsResponse("/srv", listOf("a"), false)
        repo.fetch(ListingRequest("/srv/", null))

        agentId = "agent-2"

        assertNull(repo.lookup(query("/srv/")))
        assertEquals(ListingRequest("/srv/", null), repo.nextRequest(query("/srv/")))
    }

    @Test
    fun isSupported_probesOncePerFiveMinutesPerAgent() = runTest {
        coEvery { restApi.getAgentVersion() } returns version("""{"fs":{"dirs":true}}""")

        assertTrue(repo.isSupported())
        now += 5 * 60_000L - 1
        assertTrue(repo.isSupported())
        coVerify(exactly = 1) { restApi.getAgentVersion() }

        now += 1
        assertTrue(repo.isSupported())
        agentId = "agent-2"
        assertTrue(repo.isSupported())
        coVerify(exactly = 3) { restApi.getAgentVersion() }
    }

    @Test
    fun isSupported_isFalseForAnOlderAgent() = runTest {
        coEvery { restApi.getAgentVersion() } returns version("""{"git":{"changes":true}}""")

        assertFalse(repo.isSupported())
    }

    @Test
    fun isSupported_isFalseOnAFailedProbe_andAsksAgainNextTime() = runTest {
        coEvery { restApi.getAgentVersion() } throws IOException("offline") andThen
            version("""{"fs":{"dirs":true}}""")

        assertFalse(repo.isSupported())
        assertTrue(repo.isSupported())
    }
}
