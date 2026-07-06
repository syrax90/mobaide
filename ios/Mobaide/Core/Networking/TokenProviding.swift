import Foundation

/// Boundary the auth/session layer (iOS-02) implements. iOS-01 ships only
/// these protocols plus in-memory fakes in the test target.
///
/// Semantics ported from Android `AuthInterceptor` + `SessionManager`:
/// refresh is single-flighted inside the implementation; `AgentAPI` never
/// refreshes on its own.

struct AgentToken: Equatable, Sendable {
    let value: String
    /// True when `validToken()` performed a pre-emptive refresh for this
    /// token. Suppresses the reactive 401 retry (Android `alreadyRefreshed`).
    let justRefreshed: Bool

    init(value: String, justRefreshed: Bool = false) {
        self.value = value
        self.justRefreshed = justRefreshed
    }
}

protocol AgentTokenProviding: Sendable {
    /// Returns a currently-valid access token, refreshing pre-emptively when
    /// it is expiring soon. nil means the session is terminally dead — the
    /// caller synthesizes a local 401 without touching the network. A
    /// transient refresh failure returns the stale token instead of nil.
    func validToken() async -> AgentToken?

    /// Reactive refresh after a 401 response; the result is used for exactly
    /// one retry. nil means give up (session invalidated).
    func refreshedToken() async -> String?
}

protocol UserTokenProviding: Sendable {
    /// Dashboard/user JWT for central-server calls and user-scoped WS channels.
    func userToken() async -> String?
}

protocol SessionInvalidating: Sendable {
    /// Called when the central server rejects the user JWT (401 outside the
    /// register/login/google paths). Dashboard JWTs have no refresh token,
    /// so rejection is terminal.
    func invalidateUserSession() async
}
