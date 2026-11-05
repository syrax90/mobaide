package com.mobaide.app.data.repository

/**
 * Provide a single user-facing message for the admin-login rejection,
 * shared by all screens and their tests.
 */
internal const val ADMIN_LOGIN_USER_MESSAGE =
    "This app is for agent owners. Admins should use the web dashboard."

/**
 * Signal that a login response carried ``role="admin"``.
 * Admins manage the platform from the web dashboard and must not hold
 * a session on the device.
 */
class AdminLoginNotSupportedException :
    Exception("Admin accounts are not supported on mobile. Use the web dashboard.")

/** The relay's rule for a chosen password (register, reset); login has none. */
internal const val PASSWORD_MIN_LENGTH = 8

internal const val PASSWORD_MAX_LENGTH = 128

internal const val PASSWORD_TOO_SHORT_MESSAGE =
    "Password must be at least $PASSWORD_MIN_LENGTH characters"

internal const val PASSWORD_TOO_LONG_MESSAGE =
    "Password must be at most $PASSWORD_MAX_LENGTH characters"

/**
 * The relay's length rule, counted in code points as the relay counts them,
 * or `null` when [password] meets it. Checked before sending so a register
 * 422 can only mean a bad email.
 */
internal fun passwordLengthError(password: String): String? {
    val length = password.codePointCount(0, password.length)
    return when {
        length < PASSWORD_MIN_LENGTH -> PASSWORD_TOO_SHORT_MESSAGE
        length > PASSWORD_MAX_LENGTH -> PASSWORD_TOO_LONG_MESSAGE
        else -> null
    }
}

/**
 * Signal that `/api/auth/login` answered 429: too many wrong passwords for
 * this email or from this network.
 */
class LoginRateLimitedException(val retryAfterSeconds: Int) :
    Exception("Too many sign-in attempts; retry in ${retryAfterSeconds}s")

/** Same wording as the iOS and web login screens. */
internal fun loginRateLimitedMessage(retryAfterSeconds: Int): String {
    val minutes = maxOf(1, (retryAfterSeconds + 59) / 60)
    val wait = if (minutes == 1) "1 minute" else "$minutes minutes"
    return "Too many sign-in attempts. Try again in $wait, or reset your password."
}
