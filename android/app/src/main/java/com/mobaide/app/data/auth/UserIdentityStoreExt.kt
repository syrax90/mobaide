package com.mobaide.app.data.auth

/**
 * Throw [IllegalStateException] when no user JWT is stored. Repositories that
 * gate every call on the dashboard token use this in lieu of pulling a null
 * back through coroutine boundaries; the throw is mapped to a logout flow
 * one layer up.
 */
fun UserIdentityStore.requireAccessToken(): String =
    getUserAccessToken() ?: throw IllegalStateException("User not logged in")
