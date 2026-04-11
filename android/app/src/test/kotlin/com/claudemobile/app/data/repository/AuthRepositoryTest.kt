package com.claudemobile.app.data.repository

import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.TokenResponse
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import io.mockk.verifyOrder
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Unit tests for [AuthRepository] — thin delegation layer over [RestApi] and
 * [TokenStore]. The goal is to pin the exact calls made for each public
 * method and to confirm that login persists the server URL *before* tokens
 * are written (so a concurrent reader never sees tokens without a URL).
 */
class AuthRepositoryTest {

    // ---- login --------------------------------------------------------------

    @Test
    fun login_storesServerUrl_andTokens_inOrder() = runTest {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val repo = AuthRepository(restApi, tokenStore)

        coEvery {
            restApi.login("https://example.com", "alice", "hunter2")
        } returns TokenResponse(
            access_token = "access-xyz",
            refresh_token = "refresh-xyz",
        )

        repo.login("https://example.com", "alice", "hunter2")

        // Relative order matters — isLoggedIn() depends on BOTH url and token
        // being present, so URL must land first. Enforcing the order also
        // documents that contract for future refactors.
        verifyOrder {
            tokenStore.saveServerUrl("https://example.com")
            tokenStore.saveTokens("access-xyz", "refresh-xyz")
        }
        coVerify(exactly = 1) {
            restApi.login("https://example.com", "alice", "hunter2")
        }
    }

    @Test
    fun login_propagatesApiException_andDoesNotTouchTokenStore() = runTest {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val repo = AuthRepository(restApi, tokenStore)

        coEvery {
            restApi.login(any(), any(), any())
        } throws ApiException(401, """{"detail":"Invalid credentials"}""")

        try {
            repo.login("https://example.com", "alice", "wrong")
            fail("expected ApiException to propagate")
        } catch (e: ApiException) {
            assertEquals(401, e.code)
            assertTrue(e.errorBody.contains("Invalid credentials"))
        }

        // A failed login must not leave a partial auth state behind.
        verify(exactly = 0) { tokenStore.saveServerUrl(any()) }
        verify(exactly = 0) { tokenStore.saveTokens(any(), any()) }
    }

    @Test
    fun login_passesTrailingSlashUrlThrough_unchanged() = runTest {
        // AuthRepository itself does not normalise the URL — the contract is
        // that TokenStore.saveServerUrl trims the trailing slash. This test
        // pins that AuthRepository forwards the caller-supplied value verbatim
        // to both RestApi.login and TokenStore.saveServerUrl.
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val repo = AuthRepository(restApi, tokenStore)

        coEvery {
            restApi.login("https://example.com/", "bob", "pw")
        } returns TokenResponse(access_token = "a", refresh_token = "r")

        repo.login("https://example.com/", "bob", "pw")

        verify(exactly = 1) { tokenStore.saveServerUrl("https://example.com/") }
        verify(exactly = 1) { tokenStore.saveTokens("a", "r") }
    }

    // ---- logout -------------------------------------------------------------

    @Test
    fun logout_clearsTokenStore_andDoesNothingElse() {
        val restApi = mockk<RestApi>() // intentionally no stubs — must not be touched.
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.clear() } just Runs

        AuthRepository(restApi, tokenStore).logout()

        verify(exactly = 1) { tokenStore.clear() }
    }

    // ---- isLoggedIn / delegations ------------------------------------------

    @Test
    fun isLoggedIn_delegatesToTokenStore_true() {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.isLoggedIn() } returns true

        assertTrue(AuthRepository(restApi, tokenStore).isLoggedIn())
        verify(exactly = 1) { tokenStore.isLoggedIn() }
    }

    @Test
    fun isLoggedIn_delegatesToTokenStore_false() {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.isLoggedIn() } returns false

        assertFalse(AuthRepository(restApi, tokenStore).isLoggedIn())
    }

    @Test
    fun getServerUrl_delegatesToTokenStore() {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getServerUrl() } returns "https://example.com"

        assertEquals(
            "https://example.com",
            AuthRepository(restApi, tokenStore).getServerUrl(),
        )
    }

    @Test
    fun getUsername_delegatesToTokenStore_andForwardsNull() {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getUsername() } returns null

        assertNull(AuthRepository(restApi, tokenStore).getUsername())
    }
}
