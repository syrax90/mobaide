package com.claudemobile.app.data.repository

import com.claudemobile.app.data.auth.ConnectionStore
import com.claudemobile.app.data.auth.SessionManager
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
 * Unit tests for [AuthRepository] — thin delegation layer over [RestApi],
 * [TokenStore], and [SessionManager]. The goal is to pin the exact calls
 * made for each public method, confirm that login persists the agent URL
 * **before** tokens are written (so a concurrent reader never sees tokens
 * without a URL), and that [SessionManager.markLoggedIn] is invoked
 * **after** token persistence so observers cannot race into `LoggedIn`
 * with only a URL and no bearer.
 */
class AuthRepositoryTest {

    // ---- Helpers ------------------------------------------------------------

    private fun newConnectionStore(): ConnectionStore =
        mockk<ConnectionStore>(relaxed = true)

    // ---- login --------------------------------------------------------------

    @Test
    fun login_storesServerUrl_tokens_thenMarksSessionLoggedIn_inOrder() = runTest {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)
        val repo = AuthRepository(restApi, tokenStore, sessionManager, newConnectionStore())

        coEvery {
            restApi.login("https://example.com", "alice", "hunter2")
        } returns TokenResponse(
            access_token = "access-xyz",
            refresh_token = "refresh-xyz",
        )

        repo.login("https://example.com", "alice", "hunter2")

        // URL first, then tokens (so a concurrent reader never sees
        // tokens-without-URL), then markLoggedIn() so an observer on
        // authState cannot race into LoggedIn before tokens land.
        verifyOrder {
            tokenStore.saveAgentUrl("https://example.com")
            tokenStore.saveTokens("access-xyz", "refresh-xyz")
            sessionManager.markLoggedIn()
        }
        coVerify(exactly = 1) {
            restApi.login("https://example.com", "alice", "hunter2")
        }
    }

    @Test
    fun login_propagatesApiException_andDoesNotTouchTokenStore_orSessionManager() = runTest {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)
        val repo = AuthRepository(restApi, tokenStore, sessionManager, newConnectionStore())

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
        verify(exactly = 0) { tokenStore.saveAgentUrl(any()) }
        verify(exactly = 0) { tokenStore.saveTokens(any(), any()) }
        verify(exactly = 0) { sessionManager.markLoggedIn() }
    }

    @Test
    fun login_passesTrailingSlashUrlThrough_unchanged() = runTest {
        // AuthRepository itself does not normalise the URL — the contract is
        // that TokenStore.saveAgentUrl trims the trailing slash. This test
        // pins that AuthRepository forwards the caller-supplied value verbatim
        // to both RestApi.login and TokenStore.saveAgentUrl.
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)
        val repo = AuthRepository(restApi, tokenStore, sessionManager, newConnectionStore())

        coEvery {
            restApi.login("https://example.com/", "bob", "pw")
        } returns TokenResponse(access_token = "a", refresh_token = "r")

        repo.login("https://example.com/", "bob", "pw")

        verify(exactly = 1) { tokenStore.saveAgentUrl("https://example.com/") }
        verify(exactly = 1) { tokenStore.saveTokens("a", "r") }
        verify(exactly = 1) { sessionManager.markLoggedIn() }
    }

    // ---- logout -------------------------------------------------------------

    @Test
    fun logout_delegatesToSessionManager_forceLogout() {
        // restApi is intentionally unstubbed — logout() must never touch it.
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        val sessionManager = mockk<SessionManager>()
        every { sessionManager.forceLogout() } just Runs

        AuthRepository(restApi, tokenStore, sessionManager, newConnectionStore()).logout()

        // The forceLogout contract (clear tokens + emit LoggedOut) is
        // verified in SessionManagerTest; here we only pin that the
        // repository delegates to it.
        verify(exactly = 1) { sessionManager.forceLogout() }
        verify(exactly = 0) { tokenStore.clear() }
    }

    // ---- isLoggedIn / delegations ------------------------------------------

    @Test
    fun isLoggedIn_delegatesToSessionManager_true() {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        val sessionManager = mockk<SessionManager>()
        every { sessionManager.isLoggedIn() } returns true

        assertTrue(AuthRepository(restApi, tokenStore, sessionManager, newConnectionStore()).isLoggedIn())
        verify(exactly = 1) { sessionManager.isLoggedIn() }
    }

    @Test
    fun isLoggedIn_delegatesToSessionManager_false() {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        val sessionManager = mockk<SessionManager>()
        every { sessionManager.isLoggedIn() } returns false

        assertFalse(AuthRepository(restApi, tokenStore, sessionManager, newConnectionStore()).isLoggedIn())
    }

    @Test
    fun getAgentUrl_delegatesToTokenStore() {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        val sessionManager = mockk<SessionManager>()
        every { tokenStore.getAgentUrl() } returns "https://example.com"

        assertEquals(
            "https://example.com",
            AuthRepository(restApi, tokenStore, sessionManager, newConnectionStore()).getAgentUrl(),
        )
    }

    @Test
    fun getUsername_delegatesToTokenStore_andForwardsNull() {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        val sessionManager = mockk<SessionManager>()
        every { tokenStore.getUsername() } returns null

        assertNull(AuthRepository(restApi, tokenStore, sessionManager, newConnectionStore()).getUsername())
    }
}
