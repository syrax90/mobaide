import Foundation
import os

/// Port of Android `AuthRepository`: orchestrates the central-server user
/// auth flows and the agent connect flow. Persists identity/tokens to the
/// stores first, then flips `SessionManager` state — an observer racing on
/// `fullyConnected` must never see a half-initialised session.
/// `SessionManager` owns state + refresh + logout atomicity; this type owns
/// REST calls and HTTP-error → result-type mapping.
struct AuthRepository: Sendable {
    static let roleAdmin = "admin"
    static let statusVerificationRequired = "verification_required"
    static let verificationRequiredError = "verification_required"

    let agentAPI: AgentAPI
    let centralAPI: CentralAPI
    let tokenStore: TokenStore
    let userIdentityStore: UserIdentityStore
    let sessionManager: SessionManager
    let connectionStore: ConnectionStore
    /// Shared by every copy of this struct, so the screen that verifies sees
    /// what the screen that registered remembered.
    let pendingVerification = PendingVerificationCredential()

    // MARK: - Central-server user auth

    func register(email: String, password: String) async throws -> AuthResult {
        let response = try await centralAPI.register(email: email, password: password)
        let result = try await finaliseOrFlagVerification(requestedEmail: email, response: response)
        pendingVerification.remember(result, password: password)
        return result
    }

    func loginWithPassword(email: String, password: String) async throws -> AuthResult {
        let response: UserAuthResponse
        do {
            response = try await centralAPI.login(email: email, password: password)
        } catch let error as APIError {
            if let pendingEmail = parseVerificationRequired(error) {
                let result = AuthResult.verificationRequired(email: pendingEmail)
                pendingVerification.remember(result, password: password)
                return result
            }
            throw error
        }
        let result = try await finaliseOrFlagVerification(requestedEmail: email, response: response)
        pendingVerification.remember(result, password: password)
        return result
    }

    func loginWithGoogle(idToken: String) async throws -> AuthResult {
        let response = try await centralAPI.loginWithGoogle(idToken: idToken)
        // Google OAuth always returns a verified user server-side; any
        // VerificationRequired here is unexpected and surfaces generically.
        return try await finaliseOrFlagVerification(requestedEmail: response.email, response: response)
    }

    func loginWithApple(idToken: String, nonce: String) async throws -> AuthResult {
        let response = try await centralAPI.loginWithApple(idToken: idToken, nonce: nonce)
        return try await finaliseOrFlagVerification(requestedEmail: response.email, response: response)
    }

    func verifyEmail(email: String, code: String) async throws -> AuthResult {
        let response = try await centralAPI.verifyEmail(
            email: email,
            code: code,
            password: pendingVerification.password(for: email)
        )
        pendingVerification.clear()
        if response.role == Self.roleAdmin { throw AdminLoginNotSupported() }
        guard let token = response.access_token else {
            throw APIError(code: -1, errorBody: "verify-email response missing access_token")
        }
        userIdentityStore.saveUserSession(
            accessToken: token,
            email: response.email,
            userId: response.user_id,
            role: response.role,
            scheduledDeletionAt: response.scheduled_deletion_at
        )
        await sessionManager.markUserLoggedIn()
        return .success(email: response.email, scheduledDeletionAt: response.scheduled_deletion_at)
    }

    func resendVerification(email: String) async throws -> ResendOutcome {
        do {
            _ = try await centralAPI.resendVerification(email: email)
            return .sent
        } catch let error as APIError where error.code == 429 {
            return .rateLimited(cooldownSecondsRemaining: parseRateLimitCooldown(error) ?? 60)
        }
    }

    func requestPasswordReset(email: String) async throws -> PasswordResetOutcome {
        do {
            _ = try await centralAPI.forgotPassword(email: email)
            return .sent
        } catch let error as APIError where error.code == 429 {
            return .rateLimited(retryAfterSeconds: parseResetIpRetryAfter(error) ?? 60)
        }
    }

    /// Consume a reset OTP and set a new password. Throws
    /// `ResetPasswordFailure` with a discriminated reason on any handled
    /// HTTP error.
    func resetPassword(email: String, code: String, newPassword: String) async throws {
        do {
            _ = try await centralAPI.resetPassword(email: email, code: code, newPassword: newPassword)
        } catch let error as APIError {
            throw ResetPasswordFailure(reason: mapResetError(error))
        }
    }

    // MARK: - Agent connection

    func connectAgent(agentUrl: String, username: String, password: String) async throws {
        let response = try await agentAPI.login(agentUrl: agentUrl, username: username, password: password)
        // Clear any relay-mode keys from a previous session so that
        // getConnectionMode() returns DIRECT after a direct login.
        tokenStore.clear()
        tokenStore.saveAgentUrl(agentUrl)
        tokenStore.saveTokens(accessToken: response.access_token, refreshToken: response.refresh_token)
        await sessionManager.markAgentConnected()

        connectionStore.captureActiveProfile(tokenStore: tokenStore, label: "Direct: \(agentUrl)")
    }

    func disconnectAgent() async {
        await sessionManager.agentDisconnect()
    }

    /// Remove any saved relay connection profile for `agentId`. Called when an
    /// agent is deleted so its profile is not left orphaned (and marked active)
    /// in the Connections switcher with no way to remove it.
    func removeRelayProfile(agentId: String) {
        for profile in connectionStore.listProfiles()
        where profile.mode == "relay" && profile.agentId == agentId {
            connectionStore.removeProfile(id: profile.id)
        }
    }

    /// Relay-mode connect (Android `AgentListViewModel.connectToAgent`): mint
    /// mobile relay credentials for `agentId`, persist them, flip the session to
    /// `fullyConnected` (which starts both event buses via their auth-state
    /// collectors — no explicit bus start needed), and capture or refresh the
    /// agent's connection profile. Disconnects any current agent first.
    func connectAgentViaRelay(
        agentId: String,
        friendlyName: String,
        deviceId: String,
        deviceName: String?
    ) async throws {
        guard let userToken = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: -1, errorBody: "Not signed in")
        }
        if await sessionManager.isLoggedIn() {
            await sessionManager.agentDisconnect()
        }
        let tokens = try await centralAPI.getMobileTokens(
            userToken: userToken,
            agentId: agentId,
            request: MobileTokenRequest(mobile_device_id: deviceId, mobile_device_name: deviceName, platform: "ios")
        )
        // A sign-out that landed while the mint was in flight wins: persisting
        // the relay credentials now would resurrect fullyConnected with no user.
        try Task.checkCancellation()
        let connected = await sessionManager.tryMarkAgentConnected { [tokenStore] in
            tokenStore.saveRelayCredentials(
                serverUrl: AppConfig.centralServerURL,
                agentId: agentId,
                pairingId: agentId,
                deviceId: deviceId,
                accessToken: tokens.access_token,
                refreshToken: tokens.refresh_token
            )
        }
        guard connected else {
            throw APIError(code: -1, errorBody: "Signed out while connecting")
        }

        if let existing = connectionStore.listProfiles()
            .first(where: { $0.mode == "relay" && $0.agentId == agentId }) {
            connectionStore.saveProfile(
                ConnectionStore.ConnectionProfile(
                    id: existing.id,
                    label: existing.label,
                    mode: existing.mode,
                    agentUrl: existing.agentUrl,
                    relayServerUrl: existing.relayServerUrl,
                    agentId: existing.agentId,
                    pairingId: existing.pairingId,
                    deviceId: existing.deviceId,
                    accessToken: tokens.access_token,
                    refreshToken: tokens.refresh_token,
                    createdAt: existing.createdAt
                )
            )
            connectionStore.setActiveProfileId(existing.id)
        } else {
            connectionStore.captureActiveProfile(tokenStore: tokenStore, label: "Agent: \(friendlyName)")
        }
    }

    // MARK: - Session pass-throughs

    func isUserAuthenticated() async -> Bool { await sessionManager.isUserAuthenticated() }

    func isLoggedIn() async -> Bool { await sessionManager.isLoggedIn() }

    func userLogout() async { await sessionManager.userLogout() }

    func logout() async { await sessionManager.forceLogout() }

    func getUserEmail() -> String? { userIdentityStore.getUserEmail() }

    func getAgentUrl() -> String? { tokenStore.getAgentUrl() }

    func getScheduledDeletionAt() -> String? { userIdentityStore.getScheduledDeletionAt() }

    // MARK: - Account deletion

    /// Delete with a current password as re-auth proof. On success the
    /// WS-4010 close drives the `accountRemoved` session teardown; no extra
    /// plumbing here.
    func deleteAccountWithPassword(password: String, mode: DeletionMode) async throws -> DeleteAccountResult {
        guard let token = userIdentityStore.getUserAccessToken() else {
            return .reauthFailed(message: "Not signed in")
        }
        return try await submitDelete(
            userToken: token,
            mode: mode,
            body: DeleteAccountRequest(method: "password", mode: mode.rawValue, password: password)
        )
    }

    /// Delete with a fresh Google ID token + the nonce it was fetched with
    /// (the server validates freshness, nonce match, and google_sub).
    func deleteAccountWithGoogle(idToken: String, nonce: String, mode: DeletionMode) async throws -> DeleteAccountResult {
        guard let token = userIdentityStore.getUserAccessToken() else {
            return .reauthFailed(message: "Not signed in")
        }
        return try await submitDelete(
            userToken: token,
            mode: mode,
            body: DeleteAccountRequest(method: "google", mode: mode.rawValue, id_token: idToken, nonce: nonce)
        )
    }

    /// Delete with a fresh Sign in with Apple ID token + its nonce (the server
    /// validates freshness, nonce match, and apple_sub — the `method="apple"`
    /// re-auth path landed server-side in CLA-99). Net-new for iOS: Android has
    /// no Apple delete path, but the flow mirrors the Google branch.
    func deleteAccountWithApple(idToken: String, nonce: String, mode: DeletionMode) async throws -> DeleteAccountResult {
        guard let token = userIdentityStore.getUserAccessToken() else {
            return .reauthFailed(message: "Not signed in")
        }
        return try await submitDelete(
            userToken: token,
            mode: mode,
            body: DeleteAccountRequest(method: "apple", mode: mode.rawValue, id_token: idToken, nonce: nonce)
        )
    }

    /// Cancel a pending scheduled deletion. Idempotent; `notScheduled` when
    /// the server reports no pending deletion. Clears the local marker so
    /// the grace banner disappears immediately.
    func cancelAccountDeletion() async throws -> CancelDeletionResult {
        guard let token = userIdentityStore.getUserAccessToken() else {
            return .notScheduled
        }
        let response = try await centralAPI.cancelAccountDeletion(userToken: token)
        userIdentityStore.setScheduledDeletionAt(nil)
        return response.scheduled_deletion_at == nil ? .notScheduled : .cancelled
    }

    // MARK: - Error mapping

    private func submitDelete(
        userToken: String,
        mode: DeletionMode,
        body: DeleteAccountRequest
    ) async throws -> DeleteAccountResult {
        do {
            try await centralAPI.requestAccountDeletion(userToken: userToken, request: body)
            return .submitted(mode)
        } catch let error as APIError {
            switch error.code {
            case 400: return .forbidden
            case 403: return .reauthFailed(message: parseDeleteReauthMessage(error))
            default: throw error
            }
        }
    }

    private func finaliseOrFlagVerification(
        requestedEmail: String,
        response: UserAuthResponse
    ) async throws -> AuthResult {
        if response.role == Self.roleAdmin { throw AdminLoginNotSupported() }
        guard response.status != Self.statusVerificationRequired, let token = response.access_token else {
            return .verificationRequired(email: response.email.isEmpty ? requestedEmail : response.email)
        }
        userIdentityStore.saveUserSession(
            accessToken: token,
            email: response.email,
            userId: response.user_id,
            role: response.role,
            scheduledDeletionAt: response.scheduled_deletion_at
        )
        await sessionManager.markUserLoggedIn()
        return .success(email: response.email, scheduledDeletionAt: response.scheduled_deletion_at)
    }

    private func mapResetError(_ error: APIError) -> ResetPasswordError {
        let errorTag = decodeErrorBody(ResetErrorBody.self, from: error)?.detail.error
        switch (error.code, errorTag) {
        case (410, _):
            return .expiredCode
        case (429, "reset_ip_rate_limited"):
            return .ipRateLimited(retryAfterSeconds: parseResetIpRetryAfter(error) ?? 60)
        case (429, _):
            return .attemptsExhausted
        case (400, "new_password_same_as_old"):
            return .sameAsOld
        case (400, _):
            return .invalidCode
        default:
            return .unknown(code: error.code, message: error.errorBody)
        }
    }

    private func parseVerificationRequired(_ error: APIError) -> String? {
        guard error.code == 403 else { return nil }
        guard let detail = decodeErrorBody(VerificationRequiredErrorBody.self, from: error)?.detail,
              detail.error == Self.verificationRequiredError
        else { return nil }
        return detail.email
    }

    private func parseRateLimitCooldown(_ error: APIError) -> Int? {
        guard let cooldown = decodeErrorBody(ResendRateLimitedErrorBody.self, from: error)?
            .detail.cooldown_seconds_remaining, cooldown > 0
        else { return nil }
        return cooldown
    }

    private func parseResetIpRetryAfter(_ error: APIError) -> Int? {
        guard let wait = decodeErrorBody(ResetIpRateLimitedErrorBody.self, from: error)?
            .detail.retry_after_seconds, wait > 0
        else { return nil }
        return wait
    }

    private func parseDeleteReauthMessage(_ error: APIError) -> String {
        decodeErrorBody(DeleteAccountReauthErrorBody.self, from: error)?.detail.message
            ?? "Re-authentication failed"
    }

    private func decodeErrorBody<T: Decodable>(_ type: T.Type, from error: APIError) -> T? {
        try? WireJSON.decoder().decode(T.self, from: Data(error.errorBody.utf8))
    }
}

/// The password typed on the way to the code screen, held in memory only so
/// `verifyEmail` can prove it: if someone re-registered the address in the
/// meantime, the server installs this one instead of theirs.
final class PendingVerificationCredential: Sendable {
    private struct Entry {
        let email: String
        let password: String
    }

    private let entry = OSAllocatedUnfairLock<Entry?>(initialState: nil)

    func remember(_ result: AuthResult, password: String) {
        let next: Entry?
        if case let .verificationRequired(email) = result {
            next = Entry(email: Self.normalised(email), password: password)
        } else {
            next = nil
        }
        entry.withLock { $0 = next }
    }

    func password(for email: String) -> String? {
        let key = Self.normalised(email)
        return entry.withLock { $0?.email == key ? $0?.password : nil }
    }

    func clear() {
        entry.withLock { $0 = nil }
    }

    private static func normalised(_ email: String) -> String {
        email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }
}
