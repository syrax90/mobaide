import Foundation

struct TokenRequest: Codable, Equatable, Sendable {
    let username: String
    let password: String
}

struct TokenResponse: Codable, Equatable, Sendable {
    let access_token: String
    let refresh_token: String
    let token_type: String

    init(access_token: String, refresh_token: String, token_type: String = "bearer") {
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_type = token_type
    }
}

extension TokenResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        access_token = try container.decode(String.self, forKey: .access_token)
        refresh_token = try container.decode(String.self, forKey: .refresh_token)
        token_type = try container.decodeIfPresent(String.self, forKey: .token_type) ?? "bearer"
    }
}

struct RefreshRequest: Codable, Equatable, Sendable {
    let refresh_token: String
}

struct UserRegisterRequest: Codable, Equatable, Sendable {
    let email: String
    let password: String
    /// Per-install device id; binds the session so revoking the device signs it out.
    var device_id: String? = nil
}

struct UserLoginRequest: Codable, Equatable, Sendable {
    let email: String
    let password: String
    /// Per-install device id; binds the session so revoking the device signs it out.
    var device_id: String? = nil
}

struct GoogleAuthRequest: Codable, Equatable, Sendable {
    let id_token: String
    /// Per-install device id; binds the session so revoking the device signs it out.
    var device_id: String? = nil
}

/// Unlike the Google mobile path, `nonce` is required: the client embeds it
/// verbatim in the identity token and the server verifies the match.
struct AppleAuthRequest: Codable, Equatable, Sendable {
    let id_token: String
    let nonce: String
    /// Per-install device id; binds the session so revoking the device signs it out.
    var device_id: String? = nil
}

/// `status` is "ok" for verified accounts (access_token populated) or
/// "verification_required" when the client must call /verify-email with the
/// OTP before receiving a JWT.
struct UserAuthResponse: Codable, Equatable, Sendable {
    let access_token: String?
    let token_type: String
    let user_id: String
    let email: String
    let role: String
    let status: String
    let scheduled_deletion_at: String?

    init(
        access_token: String? = nil,
        token_type: String = "bearer",
        user_id: String,
        email: String,
        role: String,
        status: String = "ok",
        scheduled_deletion_at: String? = nil
    ) {
        self.access_token = access_token
        self.token_type = token_type
        self.user_id = user_id
        self.email = email
        self.role = role
        self.status = status
        self.scheduled_deletion_at = scheduled_deletion_at
    }
}

extension UserAuthResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        access_token = try container.decodeIfPresent(String.self, forKey: .access_token)
        token_type = try container.decodeIfPresent(String.self, forKey: .token_type) ?? "bearer"
        user_id = try container.decode(String.self, forKey: .user_id)
        email = try container.decode(String.self, forKey: .email)
        role = try container.decode(String.self, forKey: .role)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "ok"
        scheduled_deletion_at = try container.decodeIfPresent(String.self, forKey: .scheduled_deletion_at)
    }
}

struct VerifyEmailRequest: Codable, Equatable, Sendable {
    let email: String
    let code: String
    /// The password typed to reach the code screen; see `AuthRepository.verifyEmail`.
    var password: String? = nil
    /// Per-install device id; binds the session so revoking the device signs it out.
    var device_id: String? = nil
}

struct ResendVerificationRequest: Codable, Equatable, Sendable {
    let email: String
}

struct ResendVerificationResponse: Codable, Equatable, Sendable {
    let status: String
    let cooldown_seconds_remaining: Int

    init(status: String, cooldown_seconds_remaining: Int = 0) {
        self.status = status
        self.cooldown_seconds_remaining = cooldown_seconds_remaining
    }
}

extension ResendVerificationResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decode(String.self, forKey: .status)
        cooldown_seconds_remaining = try container.decodeIfPresent(Int.self, forKey: .cooldown_seconds_remaining) ?? 0
    }
}

struct VerificationRequiredDetail: Codable, Equatable, Sendable {
    let error: String
    let email: String?
}

/// FastAPI 403 body from /api/auth/login for unverified accounts: `{"detail": {error, email}}`.
struct VerificationRequiredErrorBody: Codable, Equatable, Sendable {
    let detail: VerificationRequiredDetail
}

struct ResendRateLimitedDetail: Codable, Equatable, Sendable {
    let error: String
    let cooldown_seconds_remaining: Int

    init(error: String, cooldown_seconds_remaining: Int = 0) {
        self.error = error
        self.cooldown_seconds_remaining = cooldown_seconds_remaining
    }
}

extension ResendRateLimitedDetail {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        error = try container.decode(String.self, forKey: .error)
        cooldown_seconds_remaining = try container.decodeIfPresent(Int.self, forKey: .cooldown_seconds_remaining) ?? 0
    }
}

/// FastAPI 429 body from /api/auth/resend-verification: `{"detail": {error, cooldown_seconds_remaining}}`.
struct ResendRateLimitedErrorBody: Codable, Equatable, Sendable {
    let detail: ResendRateLimitedDetail
}

struct ForgotPasswordRequest: Codable, Equatable, Sendable {
    let email: String
}

struct ForgotPasswordResponse: Codable, Equatable, Sendable {
    let status: String

    init(status: String = "ok") {
        self.status = status
    }
}

extension ForgotPasswordResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "ok"
    }
}

struct ResetPasswordRequest: Codable, Equatable, Sendable {
    let email: String
    let code: String
    let new_password: String
}

struct ResetPasswordResponse: Codable, Equatable, Sendable {
    let status: String

    init(status: String = "ok") {
        self.status = status
    }
}

extension ResetPasswordResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "ok"
    }
}

struct ResetIpRateLimitedDetail: Codable, Equatable, Sendable {
    let error: String
    let retry_after_seconds: Int

    init(error: String, retry_after_seconds: Int = 0) {
        self.error = error
        self.retry_after_seconds = retry_after_seconds
    }
}

extension ResetIpRateLimitedDetail {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        error = try container.decode(String.self, forKey: .error)
        retry_after_seconds = try container.decodeIfPresent(Int.self, forKey: .retry_after_seconds) ?? 0
    }
}

/// FastAPI 429 body from /api/auth/forgot-password or /api/auth/reset-password
/// when the per-IP cap is exhausted: `{"detail": {error, retry_after_seconds}}`.
struct ResetIpRateLimitedErrorBody: Codable, Equatable, Sendable {
    let detail: ResetIpRateLimitedDetail
}

/// The `error` discriminator drives error mapping (400 from /reset-password:
/// new password equals current, or invalid code).
struct ResetErrorDetail: Codable, Equatable, Sendable {
    let error: String
}

struct ResetErrorBody: Codable, Equatable, Sendable {
    let detail: ResetErrorDetail
}

/// Exactly one of `password` (for password users) or `id_token` + `nonce`
/// (for Google users) must be populated; `mode` chooses scheduled (14-day
/// grace, default when omitted) vs immediate cascade purge.
struct DeleteAccountRequest: Codable, Equatable, Sendable {
    let method: String
    let mode: String?
    let password: String?
    let id_token: String?
    let nonce: String?

    init(
        method: String,
        mode: String? = nil,
        password: String? = nil,
        id_token: String? = nil,
        nonce: String? = nil
    ) {
        self.method = method
        self.mode = mode
        self.password = password
        self.id_token = id_token
        self.nonce = nonce
    }
}

struct CancelAccountDeletionResponse: Codable, Equatable, Sendable {
    let status: String
    let scheduled_deletion_at: String?

    init(status: String = "cancelled", scheduled_deletion_at: String? = nil) {
        self.status = status
        self.scheduled_deletion_at = scheduled_deletion_at
    }
}

extension CancelAccountDeletionResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "cancelled"
        scheduled_deletion_at = try container.decodeIfPresent(String.self, forKey: .scheduled_deletion_at)
    }
}

/// Profile behind `GET /api/auth/me`. `first_paired_at` is the never-cleared
/// first-pairing stamp the `agent_paired` conversion keys on.
struct MeResponse: Codable, Equatable, Sendable {
    let user_id: String
    let email: String
    let role: String
    let created_at: String
    let scheduled_deletion_at: String?
    let first_paired_at: String?

    init(
        user_id: String,
        email: String,
        role: String,
        created_at: String,
        scheduled_deletion_at: String? = nil,
        first_paired_at: String? = nil
    ) {
        self.user_id = user_id
        self.email = email
        self.role = role
        self.created_at = created_at
        self.scheduled_deletion_at = scheduled_deletion_at
        self.first_paired_at = first_paired_at
    }
}

struct DeleteAccountReauthDetail: Codable, Equatable, Sendable {
    let error: String
    let message: String
}

/// FastAPI 403 body from /api/auth/me/delete when credential proof is
/// rejected: `{"detail": {error: "reauth_required", message}}`.
struct DeleteAccountReauthErrorBody: Codable, Equatable, Sendable {
    let detail: DeleteAccountReauthDetail
}
