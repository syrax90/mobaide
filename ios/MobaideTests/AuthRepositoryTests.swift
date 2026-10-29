import XCTest
@testable import Mobaide

final class AuthRepositoryTests: XCTestCase {
    private var harness: SessionHarness!
    private var connectionStore: ConnectionStore!
    private var repository: AuthRepository!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        let repositoryHarness = RepositoryHarness()
        harness = repositoryHarness.session
        connectionStore = repositoryHarness.connectionStore
        repository = repositoryHarness.repository
    }

    func testLoginSuccessPersistsIdentityBeforeStateFlip() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-1","user_id":"u1","email":"user@example.com","role":"user",\
        "status":"ok","scheduled_deletion_at":"2026-07-20T00:00:00Z"}
        """)

        let result = try await repository.loginWithPassword(email: "user@example.com", password: "pw")

        XCTAssertEqual(result, .success(email: "user@example.com", scheduledDeletionAt: "2026-07-20T00:00:00Z"))
        XCTAssertEqual(harness.userIdentityStore.getUserAccessToken(), "jwt-1")
        XCTAssertEqual(harness.userIdentityStore.getUserEmail(), "user@example.com")
        XCTAssertEqual(harness.userIdentityStore.getScheduledDeletionAt(), "2026-07-20T00:00:00Z")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func testSignInSendsThisInstallsDeviceId() async throws {
        let deviceIdentityStore = DeviceIdentityStore(keychain: InMemoryKeychainStore())
        var boundRepository = repository!
        boundRepository.deviceIdentityStore = deviceIdentityStore
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-1","user_id":"u1","email":"user@example.com","role":"user"}
        """)

        _ = try await boundRepository.loginWithPassword(email: "user@example.com", password: "pw")

        let body = try XCTUnwrap(StubURLProtocol.bodies.first)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(json["device_id"] as? String, deviceIdentityStore.getOrCreateDeviceId())
    }

    func testLoginVerificationRequiredStatusFallsBackToRequestedEmail() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"user_id":"u1","email":"","role":"user","status":"verification_required"}
        """)

        let result = try await repository.loginWithPassword(email: "asked@example.com", password: "pw")

        XCTAssertEqual(result, .verificationRequired(email: "asked@example.com"))
        XCTAssertFalse(harness.userIdentityStore.isUserLoggedIn())
    }

    func testLogin403VerificationRequiredBodyMapsToVerificationRequired() async throws {
        StubURLProtocol.enqueue(status: 403, body: """
        {"detail":{"error":"verification_required","email":"pending@example.com"}}
        """)

        let result = try await repository.loginWithPassword(email: "asked@example.com", password: "pw")

        XCTAssertEqual(result, .verificationRequired(email: "pending@example.com"))
    }

    func testLogin403WithoutVerificationTagRethrows() async {
        StubURLProtocol.enqueue(status: 403, body: #"{"detail":"forbidden"}"#)

        do {
            _ = try await repository.loginWithPassword(email: "a@example.com", password: "pw")
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error.code, 403)
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testAdminRoleIsRejected() async {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-1","user_id":"u1","email":"admin@example.com","role":"admin"}
        """)

        do {
            _ = try await repository.loginWithPassword(email: "admin@example.com", password: "pw")
            XCTFail("expected AdminLoginNotSupported")
        } catch is AdminLoginNotSupported {
            XCTAssertFalse(harness.userIdentityStore.isUserLoggedIn())
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testLoginWithApplePersistsIdentityAndFlipsState() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-apple","user_id":"u3","email":"apple@example.com","role":"user"}
        """)

        let result = try await repository.loginWithApple(idToken: "apple-token", nonce: "nonce-1")

        XCTAssertEqual(result, .success(email: "apple@example.com", scheduledDeletionAt: nil))
        XCTAssertEqual(harness.userIdentityStore.getUserAccessToken(), "jwt-apple")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func testLoginWithAppleAdminRoleIsRejected() async {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt","user_id":"u1","email":"admin@example.com","role":"admin"}
        """)

        do {
            _ = try await repository.loginWithApple(idToken: "t", nonce: "n")
            XCTFail("expected AdminLoginNotSupported")
        } catch is AdminLoginNotSupported {
            XCTAssertFalse(harness.userIdentityStore.isUserLoggedIn())
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testVerifyEmailSavesSessionAndMarksUserLoggedIn() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-2","user_id":"u1","email":"user@example.com","role":"user"}
        """)

        let result = try await repository.verifyEmail(email: "user@example.com", code: "123456")

        XCTAssertEqual(result, .success(email: "user@example.com", scheduledDeletionAt: nil))
        XCTAssertEqual(harness.userIdentityStore.getUserAccessToken(), "jwt-2")
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func testResendVerificationRateLimitMapping() async throws {
        StubURLProtocol.enqueue(status: 429, body: """
        {"detail":{"error":"rate_limited","cooldown_seconds_remaining":30}}
        """)
        var outcome = try await repository.resendVerification(email: "a@example.com")
        XCTAssertEqual(outcome, .rateLimited(cooldownSecondsRemaining: 30))

        StubURLProtocol.enqueue(status: 429, body: "garbage")
        outcome = try await repository.resendVerification(email: "a@example.com")
        XCTAssertEqual(outcome, .rateLimited(cooldownSecondsRemaining: 60), "unparseable body defaults to 60")

        StubURLProtocol.enqueue(status: 200, body: #"{"status":"sent"}"#)
        outcome = try await repository.resendVerification(email: "a@example.com")
        XCTAssertEqual(outcome, .sent)
    }

    func testRequestPasswordResetRateLimitMapping() async throws {
        StubURLProtocol.enqueue(status: 429, body: """
        {"detail":{"error":"reset_ip_rate_limited","retry_after_seconds":45}}
        """)
        let outcome = try await repository.requestPasswordReset(email: "a@example.com")
        XCTAssertEqual(outcome, .rateLimited(retryAfterSeconds: 45))
    }

    func testResetPasswordErrorMapping() async {
        func reset(expecting expected: ResetPasswordError, file: StaticString = #filePath, line: UInt = #line) async {
            do {
                try await repository.resetPassword(email: "a@example.com", code: "1", newPassword: "pw")
                XCTFail("expected ResetPasswordFailure", file: file, line: line)
            } catch let failure as ResetPasswordFailure {
                XCTAssertEqual(failure.reason, expected, file: file, line: line)
            } catch {
                XCTFail("unexpected error \(error)", file: file, line: line)
            }
        }

        StubURLProtocol.enqueue(status: 410, body: #"{"detail":{"error":"expired"}}"#)
        await reset(expecting: .expiredCode)

        StubURLProtocol.enqueue(status: 429, body: """
        {"detail":{"error":"reset_ip_rate_limited","retry_after_seconds":30}}
        """)
        await reset(expecting: .ipRateLimited(retryAfterSeconds: 30))

        StubURLProtocol.enqueue(status: 429, body: #"{"detail":{"error":"too_many_attempts"}}"#)
        await reset(expecting: .attemptsExhausted)

        StubURLProtocol.enqueue(status: 400, body: #"{"detail":{"error":"new_password_same_as_old"}}"#)
        await reset(expecting: .sameAsOld)

        StubURLProtocol.enqueue(status: 400, body: #"{"detail":{"error":"invalid_code"}}"#)
        await reset(expecting: .invalidCode)

        StubURLProtocol.enqueue(status: 503, body: "down")
        await reset(expecting: .unknown(code: 503, message: "down"))
    }

    func testDeleteAccountMapping() async throws {
        harness.userIdentityStore.saveUserSession(
            accessToken: "jwt", email: "a@example.com", userId: "u1", role: "user"
        )

        StubURLProtocol.enqueue(status: 400, body: #"{"detail":"google-only"}"#)
        var result = try await repository.deleteAccountWithPassword(password: "pw", mode: .scheduled)
        XCTAssertEqual(result, .forbidden)

        StubURLProtocol.enqueue(status: 403, body: """
        {"detail":{"error":"reauth_required","message":"Wrong password"}}
        """)
        result = try await repository.deleteAccountWithPassword(password: "pw", mode: .immediate)
        XCTAssertEqual(result, .reauthFailed(message: "Wrong password"))

        StubURLProtocol.enqueue(status: 204, body: "")
        result = try await repository.deleteAccountWithGoogle(idToken: "id", nonce: "n", mode: .scheduled)
        XCTAssertEqual(result, .submitted(.scheduled))
    }

    func testDeleteAccountWithoutSessionFailsLocally() async throws {
        let result = try await repository.deleteAccountWithPassword(password: "pw", mode: .scheduled)
        XCTAssertEqual(result, .reauthFailed(message: "Not signed in"))
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "no network call without a user token")
    }

    func testCancelAccountDeletionClearsLocalMarker() async throws {
        harness.userIdentityStore.saveUserSession(
            accessToken: "jwt", email: "a@example.com", userId: "u1", role: "user",
            scheduledDeletionAt: "2026-07-20T00:00:00Z"
        )

        StubURLProtocol.enqueue(status: 200, body: """
        {"status":"cancelled","scheduled_deletion_at":"2026-07-20T00:00:00Z"}
        """)
        let result = try await repository.cancelAccountDeletion()
        XCTAssertEqual(result, .cancelled)
        XCTAssertNil(harness.userIdentityStore.getScheduledDeletionAt())

        StubURLProtocol.enqueue(status: 200, body: #"{"status":"noop","scheduled_deletion_at":null}"#)
        let idempotent = try await repository.cancelAccountDeletion()
        XCTAssertEqual(idempotent, .notScheduled)
    }

    func testConnectAgentPersistsThenFlipsAndCapturesProfile() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"agent-at","refresh_token":"agent-rt","token_type":"bearer"}
        """)

        try await repository.connectAgent(
            agentUrl: "https://agent.example.com",
            username: "admin",
            password: "pw"
        )

        XCTAssertEqual(harness.tokenStore.getAgentUrl(), "https://agent.example.com")
        XCTAssertEqual(harness.tokenStore.getAccessToken(), "agent-at")
        XCTAssertEqual(harness.tokenStore.getConnectionMode(), .direct)
        let state = await harness.sessionManager.currentAuthState
        XCTAssertEqual(state, .fullyConnected)

        let profiles = connectionStore.listProfiles()
        XCTAssertEqual(profiles.count, 1)
        XCTAssertEqual(profiles.first?.label, "Direct: https://agent.example.com")
        XCTAssertEqual(profiles.first?.accessToken, "agent-at")
        XCTAssertEqual(connectionStore.getActiveProfileId(), profiles.first?.id)
    }

    func testConnectAgentViaRelayUpdatesExistingProfileInPlace() async throws {
        let harness = RepositoryHarness(session: SessionHarness(userLoggedIn: true))
        let existing = ConnectionStore.ConnectionProfile(
            id: "profile-a1",
            label: "Agent: Old Name",
            mode: "relay",
            relayServerUrl: "https://central.example.com",
            agentId: "a1",
            pairingId: "a1",
            deviceId: "dev-1",
            accessToken: "stale-at",
            refreshToken: "stale-rt"
        )
        harness.connectionStore.saveProfile(existing)

        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"fresh-at","refresh_token":"fresh-rt","token_type":"bearer"}
        """)
        try await harness.repository.connectAgentViaRelay(
            agentId: "a1", friendlyName: "New Name", deviceId: "dev-1", deviceName: "iPhone"
        )

        XCTAssertEqual(harness.session.tokenStore.getConnectionMode(), .relay)
        XCTAssertEqual(harness.session.tokenStore.getAccessToken(), "fresh-at")
        let state = await harness.session.sessionManager.currentAuthState
        XCTAssertEqual(state, .fullyConnected)

        let profiles = harness.connectionStore.listProfiles()
        XCTAssertEqual(profiles.count, 1, "existing relay profile is updated in place, not duplicated")
        XCTAssertEqual(profiles.first?.id, "profile-a1")
        XCTAssertEqual(profiles.first?.accessToken, "fresh-at")
        XCTAssertEqual(profiles.first?.refreshToken, "fresh-rt")
        XCTAssertEqual(profiles.first?.label, "Agent: Old Name", "label preserved on update")
        XCTAssertEqual(harness.connectionStore.getActiveProfileId(), "profile-a1")
    }

    func testConnectAgentViaRelayWithoutUserTokenThrows() async {
        let harness = RepositoryHarness(session: SessionHarness(userLoggedIn: false))
        do {
            try await harness.repository.connectAgentViaRelay(
                agentId: "a1", friendlyName: "A", deviceId: "d", deviceName: nil
            )
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error.code, -1)
            XCTAssertTrue(StubURLProtocol.requests.isEmpty, "no network call without a user token")
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testRemoveRelayProfileDropsOnlyTheMatchingProfile() {
        let harness = RepositoryHarness(session: SessionHarness(userLoggedIn: true))
        harness.session.tokenStore.saveRelayCredentials(
            serverUrl: "https://central.example.com",
            agentId: "a1",
            pairingId: "a1",
            deviceId: "d",
            accessToken: "at",
            refreshToken: "rt"
        )
        harness.connectionStore.captureActiveProfile(tokenStore: harness.session.tokenStore, label: "Agent: a1")
        XCTAssertEqual(harness.connectionStore.listProfiles().count, 1)

        harness.repository.removeRelayProfile(agentId: "other")  // no match → unchanged
        XCTAssertEqual(harness.connectionStore.listProfiles().count, 1)

        harness.repository.removeRelayProfile(agentId: "a1")  // match → removed + active cleared
        XCTAssertTrue(harness.connectionStore.listProfiles().isEmpty)
        XCTAssertNil(harness.connectionStore.getActiveProfileId())
    }
}
