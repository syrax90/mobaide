import Foundation

/// Outcome of register / login / verify-email.
enum AuthResult: Equatable, Sendable {
    /// Tokens persisted, session active. `scheduledDeletionAt` is non-nil
    /// when the account is in the soft-delete grace window.
    case success(email: String, scheduledDeletionAt: String?)
    /// The account is not yet verified; route to the verify-email screen.
    case verificationRequired(email: String)
}

enum ResendOutcome: Equatable, Sendable {
    case sent
    case rateLimited(cooldownSecondsRemaining: Int)
}

/// The server always answers forgot-password with 200 to defeat
/// enumeration; only the per-IP 429 cap surfaces.
enum PasswordResetOutcome: Equatable, Sendable {
    case sent
    case rateLimited(retryAfterSeconds: Int)
}

/// Discriminated /reset-password failure, decoded from the FastAPI
/// `{"detail":{"error": ...}}` body.
enum ResetPasswordError: Equatable, Sendable {
    case invalidCode
    case expiredCode
    case attemptsExhausted
    case sameAsOld
    case ipRateLimited(retryAfterSeconds: Int)
    case unknown(code: Int, message: String)
}

struct ResetPasswordFailure: Error, Equatable, Sendable {
    let reason: ResetPasswordError
}

/// Scheduled (14-day soft-delete grace, cancellable) vs immediate cascade
/// purge. Raw values are the wire strings.
enum DeletionMode: String, Equatable, Sendable {
    case scheduled
    case immediate
}

enum DeleteAccountResult: Equatable, Sendable {
    case submitted(DeletionMode)
    case reauthFailed(message: String)
    case forbidden
}

enum CancelDeletionResult: Equatable, Sendable {
    case cancelled
    case notScheduled
}

/// Admin accounts must use the web dashboard, never the mobile client.
struct AdminLoginNotSupported: Error, Equatable, Sendable {}
