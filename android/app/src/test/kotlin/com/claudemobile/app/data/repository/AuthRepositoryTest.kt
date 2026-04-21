package com.claudemobile.app.data.repository

import com.claudemobile.app.data.auth.ConnectionStore
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.auth.UserIdentityStore
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.network.CentralApi
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.ResendVerificationResponse
import com.claudemobile.app.data.network.dto.TokenResponse
import com.claudemobile.app.data.network.dto.UserAuthResponse
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
 * Unit tests for [AuthRepository] — covers both user-identity operations
 * (register, loginWithPassword, loginWithGoogle) and agent-connection
 * operations (connectAgent, disconnectAgent, logout).
 */
class AuthRepositoryTest {

    // ---- Helpers ------------------------------------------------------------

    private fun newConnectionStore(): ConnectionStore =
        mockk<ConnectionStore>(relaxed = true)

    private fun newUserIdentityStore(): UserIdentityStore =
        mockk<UserIdentityStore>(relaxUnitFun = true)

    private fun newCentralApi(): CentralApi = mockk<CentralApi>()

    private fun newRepo(
        restApi: RestApi = mockk(),
        centralApi: CentralApi = newCentralApi(),
        tokenStore: TokenStore = mockk(relaxUnitFun = true),
        userIdentityStore: UserIdentityStore = newUserIdentityStore(),
        sessionManager: SessionManager = mockk(relaxUnitFun = true),
        connectionStore: ConnectionStore = newConnectionStore(),
    ) = AuthRepository(restApi, centralApi, tokenStore, userIdentityStore, sessionManager, connectionStore)

    // ---- register -----------------------------------------------------------

    @Test
    fun register_savesToUserIdentityStore_thenMarksUserLoggedIn() = runTest {
        val centralApi = newCentralApi()
        val userStore = newUserIdentityStore()
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)

        coEvery { centralApi.register("alice@test.com", "pw") } returns UserAuthResponse(
            access_token = "user-token",
            user_id = "u1",
            email = "alice@test.com",
            role = "viewer",
        )

        val repo = newRepo(centralApi = centralApi, userIdentityStore = userStore, sessionManager = sessionManager)
        repo.register("alice@test.com", "pw")

        verifyOrder {
            userStore.saveUserSession("user-token", "alice@test.com", "u1", "viewer")
            sessionManager.markUserLoggedIn()
        }
    }

    @Test
    fun register_propagatesApiException() = runTest {
        val centralApi = newCentralApi()
        coEvery { centralApi.register(any(), any()) } throws ApiException(409, "Email taken")

        val repo = newRepo(centralApi = centralApi)
        try {
            repo.register("dup@test.com", "pw")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(409, e.code)
        }
    }

    // ---- loginWithPassword --------------------------------------------------

    @Test
    fun loginWithPassword_savesToUserIdentityStore_thenMarksUserLoggedIn() = runTest {
        val centralApi = newCentralApi()
        val userStore = newUserIdentityStore()
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)

        coEvery { centralApi.login("bob@test.com", "pw") } returns UserAuthResponse(
            access_token = "user-token-2",
            user_id = "u2",
            email = "bob@test.com",
            role = "viewer",
        )

        val repo = newRepo(centralApi = centralApi, userIdentityStore = userStore, sessionManager = sessionManager)
        repo.loginWithPassword("bob@test.com", "pw")

        verifyOrder {
            userStore.saveUserSession("user-token-2", "bob@test.com", "u2", "viewer")
            sessionManager.markUserLoggedIn()
        }
    }

    @Test
    fun loginWithPassword_on401_propagatesApiException() = runTest {
        val centralApi = newCentralApi()
        coEvery { centralApi.login(any(), any()) } throws ApiException(401, "Invalid credentials")

        val repo = newRepo(centralApi = centralApi)
        try {
            repo.loginWithPassword("x@test.com", "wrong")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(401, e.code)
        }
    }

    // ---- loginWithGoogle ----------------------------------------------------

    @Test
    fun loginWithGoogle_savesToUserIdentityStore_thenMarksUserLoggedIn() = runTest {
        val centralApi = newCentralApi()
        val userStore = newUserIdentityStore()
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)

        coEvery { centralApi.loginWithGoogle("google-id-token") } returns UserAuthResponse(
            access_token = "user-token-3",
            user_id = "u3",
            email = "carol@gmail.com",
            role = "viewer",
        )

        val repo = newRepo(centralApi = centralApi, userIdentityStore = userStore, sessionManager = sessionManager)
        repo.loginWithGoogle("google-id-token")

        verifyOrder {
            userStore.saveUserSession("user-token-3", "carol@gmail.com", "u3", "viewer")
            sessionManager.markUserLoggedIn()
        }
    }

    // ---- connectAgent -------------------------------------------------------

    @Test
    fun connectAgent_storesAgentUrl_tokens_thenMarksAgentConnected() = runTest {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)
        val repo = newRepo(restApi = restApi, tokenStore = tokenStore, sessionManager = sessionManager)

        coEvery {
            restApi.login("https://example.com", "alice", "hunter2")
        } returns TokenResponse(
            access_token = "access-xyz",
            refresh_token = "refresh-xyz",
        )

        repo.connectAgent("https://example.com", "alice", "hunter2")

        verifyOrder {
            tokenStore.saveAgentUrl("https://example.com")
            tokenStore.saveTokens("access-xyz", "refresh-xyz")
            sessionManager.markAgentConnected()
        }
    }

    @Test
    fun connectAgent_propagatesApiException_andDoesNotTouchTokenStore() = runTest {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>(relaxUnitFun = true)
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)
        val repo = newRepo(restApi = restApi, tokenStore = tokenStore, sessionManager = sessionManager)

        coEvery {
            restApi.login(any(), any(), any())
        } throws ApiException(401, """{"detail":"Invalid credentials"}""")

        try {
            repo.connectAgent("https://example.com", "alice", "wrong")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(401, e.code)
        }

        // The first clear() call in connectAgent doesn't happen because the
        // exception is thrown by restApi.login() which is the first call.
        // But we check that saveAgentUrl/saveTokens/markAgentConnected are not called.
        verify(exactly = 0) { tokenStore.saveAgentUrl(any()) }
        verify(exactly = 0) { tokenStore.saveTokens(any(), any()) }
        verify(exactly = 0) { sessionManager.markAgentConnected() }
    }

    // ---- logout / disconnect ------------------------------------------------

    @Test
    fun logout_delegatesToSessionManager_forceLogout() {
        val sessionManager = mockk<SessionManager>()
        every { sessionManager.forceLogout() } just Runs

        newRepo(sessionManager = sessionManager).logout()

        verify(exactly = 1) { sessionManager.forceLogout() }
    }

    @Test
    fun userLogout_delegatesToSessionManager_userLogout() {
        val sessionManager = mockk<SessionManager>()
        every { sessionManager.userLogout() } just Runs

        newRepo(sessionManager = sessionManager).userLogout()

        verify(exactly = 1) { sessionManager.userLogout() }
    }

    @Test
    fun disconnectAgent_delegatesToSessionManager_agentDisconnect() {
        val sessionManager = mockk<SessionManager>()
        every { sessionManager.agentDisconnect() } just Runs

        newRepo(sessionManager = sessionManager).disconnectAgent()

        verify(exactly = 1) { sessionManager.agentDisconnect() }
    }

    // ---- isLoggedIn / delegations ------------------------------------------

    @Test
    fun isLoggedIn_delegatesToSessionManager_true() {
        val sessionManager = mockk<SessionManager>()
        every { sessionManager.isLoggedIn() } returns true

        assertTrue(newRepo(sessionManager = sessionManager).isLoggedIn())
    }

    @Test
    fun isLoggedIn_delegatesToSessionManager_false() {
        val sessionManager = mockk<SessionManager>()
        every { sessionManager.isLoggedIn() } returns false

        assertFalse(newRepo(sessionManager = sessionManager).isLoggedIn())
    }

    @Test
    fun getAgentUrl_delegatesToTokenStore() {
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getAgentUrl() } returns "https://example.com"

        assertEquals("https://example.com", newRepo(tokenStore = tokenStore).getAgentUrl())
    }

    @Test
    fun getUsername_delegatesToTokenStore_andForwardsNull() {
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getUsername() } returns null

        assertNull(newRepo(tokenStore = tokenStore).getUsername())
    }

    @Test
    fun getUserEmail_delegatesToUserIdentityStore() {
        val userStore = newUserIdentityStore()
        every { userStore.getUserEmail() } returns "user@test.com"

        assertEquals("user@test.com", newRepo(userIdentityStore = userStore).getUserEmail())
    }

    // ---- Admin login is blocked on mobile -----------------------------------

    @Test
    fun loginWithPassword_admin_throws_andDoesNotPersistSession() = runTest {
        val centralApi = newCentralApi()
        val userStore = newUserIdentityStore()
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)

        coEvery { centralApi.login("root@test.com", "pw") } returns UserAuthResponse(
            access_token = "admin-token",
            user_id = "a1",
            email = "root@test.com",
            role = "admin",
        )

        val repo = newRepo(centralApi = centralApi, userIdentityStore = userStore, sessionManager = sessionManager)
        try {
            repo.loginWithPassword("root@test.com", "pw")
            fail("expected AdminLoginNotSupportedException")
        } catch (_: AdminLoginNotSupportedException) {
            // expected
        }

        verify(exactly = 0) { userStore.saveUserSession(any(), any(), any(), any()) }
        verify(exactly = 0) { sessionManager.markUserLoggedIn() }
    }

    @Test
    fun loginWithGoogle_admin_throws_andDoesNotPersistSession() = runTest {
        val centralApi = newCentralApi()
        val userStore = newUserIdentityStore()
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)

        coEvery { centralApi.loginWithGoogle("google-id-token") } returns UserAuthResponse(
            access_token = "admin-token",
            user_id = "a2",
            email = "root@gmail.com",
            role = "admin",
        )

        val repo = newRepo(centralApi = centralApi, userIdentityStore = userStore, sessionManager = sessionManager)
        try {
            repo.loginWithGoogle("google-id-token")
            fail("expected AdminLoginNotSupportedException")
        } catch (_: AdminLoginNotSupportedException) {
            // expected
        }

        verify(exactly = 0) { userStore.saveUserSession(any(), any(), any(), any()) }
        verify(exactly = 0) { sessionManager.markUserLoggedIn() }
    }

    // ---- Email verification -------------------------------------------------

    @Test
    fun register_whenVerificationRequired_doesNotPersistSession() = runTest {
        val centralApi = newCentralApi()
        val userStore = newUserIdentityStore()
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)

        coEvery { centralApi.register("pending@test.com", "pw") } returns UserAuthResponse(
            access_token = null,
            user_id = "u7",
            email = "pending@test.com",
            role = "viewer",
            status = "verification_required",
        )

        val repo = newRepo(centralApi = centralApi, userIdentityStore = userStore, sessionManager = sessionManager)
        val result = repo.register("pending@test.com", "pw")

        assertTrue(result is AuthResult.VerificationRequired)
        assertEquals(
            "pending@test.com",
            (result as AuthResult.VerificationRequired).email,
        )
        verify(exactly = 0) { userStore.saveUserSession(any(), any(), any(), any()) }
        verify(exactly = 0) { sessionManager.markUserLoggedIn() }
    }

    @Test
    fun verifyEmail_success_savesSession_andMarksLoggedIn() = runTest {
        val centralApi = newCentralApi()
        val userStore = newUserIdentityStore()
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)

        coEvery { centralApi.verifyEmail("v@test.com", "123456") } returns UserAuthResponse(
            access_token = "verified-token",
            user_id = "u8",
            email = "v@test.com",
            role = "viewer",
        )

        val repo = newRepo(centralApi = centralApi, userIdentityStore = userStore, sessionManager = sessionManager)
        val result = repo.verifyEmail("v@test.com", "123456")

        assertEquals("v@test.com", result.email)
        verifyOrder {
            userStore.saveUserSession("verified-token", "v@test.com", "u8", "viewer")
            sessionManager.markUserLoggedIn()
        }
    }

    @Test
    fun loginWithPassword_on403VerificationRequired_returnsVerificationRequired() = runTest {
        val centralApi = newCentralApi()
        val userStore = newUserIdentityStore()
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)

        val body = """{"detail":{"error":"verification_required","email":"p@test.com"}}"""
        coEvery { centralApi.login("p@test.com", "pw") } throws ApiException(403, body)

        val repo = newRepo(centralApi = centralApi, userIdentityStore = userStore, sessionManager = sessionManager)
        val result = repo.loginWithPassword("p@test.com", "pw")

        assertTrue(result is AuthResult.VerificationRequired)
        assertEquals("p@test.com", (result as AuthResult.VerificationRequired).email)
        verify(exactly = 0) { userStore.saveUserSession(any(), any(), any(), any()) }
        verify(exactly = 0) { sessionManager.markUserLoggedIn() }
    }

    @Test
    fun resendVerification_onSent_returnsSent() = runTest {
        val centralApi = newCentralApi()
        coEvery { centralApi.resendVerification("r@test.com") } returns
            ResendVerificationResponse(status = "sent")

        val result = newRepo(centralApi = centralApi).resendVerification("r@test.com")

        assertEquals(ResendOutcome.Sent, result)
    }

    @Test
    fun resendVerification_on429_returnsRateLimitedWithCooldown() = runTest {
        val centralApi = newCentralApi()
        val body = """{"detail":{"error":"resend_rate_limited","cooldown_seconds_remaining":42}}"""
        coEvery { centralApi.resendVerification("r2@test.com") } throws ApiException(429, body)

        val result = newRepo(centralApi = centralApi).resendVerification("r2@test.com")

        assertTrue(result is ResendOutcome.RateLimited)
        assertEquals(42, (result as ResendOutcome.RateLimited).cooldownSecondsRemaining)
    }

    @Test
    fun register_admin_throws_andDoesNotPersistSession() = runTest {
        val centralApi = newCentralApi()
        val userStore = newUserIdentityStore()
        val sessionManager = mockk<SessionManager>(relaxUnitFun = true)

        coEvery { centralApi.register("root@test.com", "pw") } returns UserAuthResponse(
            access_token = "admin-token",
            user_id = "a3",
            email = "root@test.com",
            role = "admin",
        )

        val repo = newRepo(centralApi = centralApi, userIdentityStore = userStore, sessionManager = sessionManager)
        try {
            repo.register("root@test.com", "pw")
            fail("expected AdminLoginNotSupportedException")
        } catch (_: AdminLoginNotSupportedException) {
            // expected
        }

        verify(exactly = 0) { userStore.saveUserSession(any(), any(), any(), any()) }
        verify(exactly = 0) { sessionManager.markUserLoggedIn() }
    }
}
