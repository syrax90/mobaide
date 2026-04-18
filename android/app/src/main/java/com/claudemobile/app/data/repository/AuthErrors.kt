package com.claudemobile.app.data.repository

/**
 * User-facing message shown by LoginViewModel / RegisterViewModel when
 * [AdminLoginNotSupportedException] is caught. Centralised so that both
 * screens (and their tests) share a single source of truth.
 */
internal const val ADMIN_LOGIN_USER_MESSAGE =
    "This app is for agent owners. Admins should use the web dashboard."

/**
 * Raised by [AuthRepository] when a login response carries
 * ``role="admin"``. The mobile app is for agent owners (viewers);
 * admins manage the platform from the web dashboard and must not hold
 * a session on the device.
 */
class AdminLoginNotSupportedException :
    Exception("Admin accounts are not supported on mobile. Use the web dashboard.")
