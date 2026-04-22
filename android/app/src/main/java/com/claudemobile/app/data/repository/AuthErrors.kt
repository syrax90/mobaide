package com.claudemobile.app.data.repository

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
