import Foundation

/// Combined auth state over two independent axes: **user identity**
/// (central server) and **agent connection** (per-agent tokens).
enum AuthState: Equatable, Sendable {
    /// No user identity on file.
    case unauthenticated
    /// User logged in to the central server, no agent connection.
    case userOnly
    /// User logged in AND agent connection established.
    case fullyConnected
    /// Server reports a wire contract this build cannot speak. The
    /// navigation graph collapses to the upgrade takeover and never
    /// advances out of this state without a manual app update.
    case incompatibleServer(IncompatibleServer)

    struct IncompatibleServer: Equatable, Sendable {
        let reason: String
        let minClientVersion: Int
        let storeUrl: String
        let storeFallbackUrl: String
    }

    static func initial(tokenStore: TokenStore, userIdentityStore: UserIdentityStore) -> AuthState {
        if userIdentityStore.isUserLoggedIn() && tokenStore.isLoggedIn() {
            return .fullyConnected
        }
        if userIdentityStore.isUserLoggedIn() {
            return .userOnly
        }
        return .unauthenticated
    }
}

/// Distinguish terminal from transient refresh failures so callers can
/// decide whether to surface "session expired" (terminal) or "network
/// flake" (transient).
enum RefreshResult: Equatable, Sendable {
    /// Tokens rotated successfully.
    case success
    /// Refresh rejected (401) or no refresh token on file — session is dead.
    case terminal
    /// Network/5xx error — tokens are still believed valid, caller may retry.
    case transient
}

/// Why a session was invalidated, so consumers can surface "session
/// expired" (tokenRejected), "your account was removed" (accountRemoved),
/// or stay silent (manual logout).
enum InvalidationReason: Equatable, Sendable {
    /// Server rejected the dashboard JWT (401 on REST, 4001 on WS).
    case tokenRejected
    /// Server closed the user-scoped WS with code 4010 — the account was
    /// destructively unregistered (by an admin or a self-delete request).
    case accountRemoved
    /// User explicitly tapped logout.
    case manual
}

/// Port of Android `SessionManager`: owns the authenticated session
/// lifecycle — observable `authState`, single-flight token refresh, and
/// atomic logout.
///
/// State machine invariants (Android `@Synchronized` parity — every
/// transition below runs isolated on this actor with no suspension between
/// store mutation and state publication):
/// - `invalidateUserSession` is idempotent: a no-op while already
///   `unauthenticated`, so the REST-401 / WS-4001 cold-start race emits the
///   "session expired" event exactly once.
/// - `agentDisconnect` always clears agent tokens but only demotes the
///   state when currently `fullyConnected`.
/// - Only `.tokenRejected` / `.accountRemoved` emit on `sessionEvents`
///   (replay-1 so a pre-subscription invalidation is still delivered);
///   `.manual` is silent. `markUserLoggedIn` clears the replay cache so a
///   stale rejection cannot re-fire after a successful re-login.
///
/// Refresh invariants:
/// - Single-flight: the in-flight `Task` itself is cached in actor state
///   (created and stored with no interleaving suspension) so N concurrent
///   callers coalesce onto exactly one network request. Caching only the
///   result would double-fire under actor reentrancy.
/// - A terminal refresh clears agent tokens only (`userOnly` if a user
///   identity remains).
/// - Every refresh outcome is generation-gated: a competing transition
///   (`invalidateUserSession`, `agentDisconnect`, `markIncompatibleServer`)
///   bumps the generation when it cancels the in-flight task, so a stale
///   result landing after the transition can neither re-promote the state
///   to `fullyConnected` nor demote an `incompatibleServer` takeover — it
///   surfaces to its awaiting callers as `.terminal` with no side effects.
actor SessionManager {
    typealias RefreshTransport = @Sendable (URLRequest) async throws -> (Data, HTTPURLResponse)

    private let tokenStore: TokenStore
    private let userIdentityStore: UserIdentityStore
    private let refreshTransport: RefreshTransport

    private let authStateBroadcaster = Broadcaster<AuthState>.stateFlow()
    private let sessionEventsBroadcaster = Broadcaster<InvalidationReason>(replay: .latestClearable)

    private var state: AuthState
    private var inFlightRefresh: Task<RefreshResult, Never>?
    private var refreshGeneration = 0

    init(
        tokenStore: TokenStore,
        userIdentityStore: UserIdentityStore,
        initialState: AuthState? = nil,
        refreshTransport: RefreshTransport? = nil
    ) {
        self.tokenStore = tokenStore
        self.userIdentityStore = userIdentityStore
        self.refreshTransport = refreshTransport ?? Self.defaultRefreshTransport()
        let initial = initialState
            ?? AuthState.initial(tokenStore: tokenStore, userIdentityStore: userIdentityStore)
        self.state = initial
        authStateBroadcaster.emit(initial)
    }

    // MARK: - Observation

    /// Current-state-replaying stream (Kotlin `StateFlow` parity).
    nonisolated func authStates() -> AsyncStream<AuthState> {
        authStateBroadcaster.subscribe(replayLatest: true)
    }

    /// Changes-only stream (Kotlin `authState.drop(1)` parity, without the
    /// race of dropping a real post-subscription transition).
    nonisolated func authStateChanges() -> AsyncStream<AuthState> {
        authStateBroadcaster.subscribe(replayLatest: false)
    }

    nonisolated func sessionEvents() -> AsyncStream<InvalidationReason> {
        sessionEventsBroadcaster.subscribe(replayLatest: true)
    }

    var currentAuthState: AuthState { state }

    /// True only when the agent connection is fully established
    /// (backward-compatible Android `isLoggedIn` semantic).
    func isLoggedIn() -> Bool { state == .fullyConnected }

    /// True when at least a user identity exists (userOnly or fullyConnected).
    func isUserAuthenticated() -> Bool { state == .userOnly || state == .fullyConnected }

    // MARK: - Transitions

    /// Mark the user as logged in after a successful register/login/verify
    /// against the central server.
    func markUserLoggedIn() {
        sessionEventsBroadcaster.clearReplay()
        setState(.userOnly)
    }

    /// Mark the agent connection as established — call only after tokens
    /// have been persisted to `TokenStore`.
    func markAgentConnected() {
        setState(.fullyConnected)
    }

    /// Pin the session to the terminal upgrade-required state. Cleared only
    /// by a successful version-gate probe (iOS-08).
    func markIncompatibleServer(_ info: AuthState.IncompatibleServer) {
        setState(.incompatibleServer(info))
        cancelInFlightRefresh()
    }

    /// Disconnect from the current agent but keep the user identity.
    func agentDisconnect() {
        tokenStore.clear()
        if state == .fullyConnected {
            setState(.userOnly)
        }
        cancelInFlightRefresh()
    }

    /// Full logout: clears user identity AND agent tokens, silently.
    func userLogout() {
        invalidateUserSession(.manual)
    }

    /// Legacy alias for `userLogout` (Android parity).
    func forceLogout() {
        invalidateUserSession(.manual)
    }

    /// Atomically clear the entire session and signal why. Idempotent while
    /// already `unauthenticated` (no re-emit).
    func invalidateUserSession(_ reason: InvalidationReason) {
        if state == .unauthenticated { return }
        userIdentityStore.clearUserSession()
        tokenStore.clear()
        setState(.unauthenticated)
        cancelInFlightRefresh()
        switch reason {
        case .tokenRejected, .accountRemoved:
            sessionEventsBroadcaster.emit(reason)
        case .manual:
            break
        }
    }

    // MARK: - Refresh

    /// Single-flight refresh: concurrent callers share one network request.
    func refresh() async -> RefreshResult {
        if let existing = inFlightRefresh {
            return await existing.value
        }
        refreshGeneration += 1
        let generation = refreshGeneration
        let task = Task { () -> RefreshResult in
            defer { clearInFlightRefresh(ifGeneration: generation) }
            return await performRefresh(generation: generation)
        }
        inFlightRefresh = task
        return await task.value
    }

    private func clearInFlightRefresh(ifGeneration generation: Int) {
        if refreshGeneration == generation {
            inFlightRefresh = nil
        }
    }

    private func cancelInFlightRefresh() {
        refreshGeneration += 1
        inFlightRefresh?.cancel()
        inFlightRefresh = nil
    }

    private func performRefresh(generation: Int) async -> RefreshResult {
        guard let refreshToken = tokenStore.getRefreshToken() else {
            return terminalAgentOnly(generation: generation)
        }

        let refreshUrl: String
        switch tokenStore.getConnectionMode() {
        case .direct:
            guard let agentUrl = tokenStore.getAgentUrl() else {
                return terminalAgentOnly(generation: generation)
            }
            refreshUrl = "\(agentUrl)/api/auth/refresh"
        case .relay:
            guard let serverUrl = tokenStore.getRelayServerUrl() else {
                return terminalAgentOnly(generation: generation)
            }
            refreshUrl = "\(serverUrl)/api/mobile/refresh"
        }

        guard
            let url = URL(string: refreshUrl),
            let body = try? WireJSON.encoder().encode(RefreshRequest(refresh_token: refreshToken))
        else {
            return .transient
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        do {
            let (data, response) = try await refreshTransport(request)
            if (200...299).contains(response.statusCode) {
                guard
                    !data.isEmpty,
                    let dto = try? WireJSON.decoder().decode(TokenResponse.self, from: data)
                else {
                    return .transient
                }
                return applyRefreshSuccess(
                    access: dto.access_token,
                    refresh: dto.refresh_token,
                    generation: generation
                )
            }
            if response.statusCode == 401 {
                return terminalAgentOnly(generation: generation)
            }
            return .transient
        } catch {
            // A refresh cancelled by a competing transition surfaces to
            // awaiting callers as terminal (Android: cancelled Deferred
            // throws on await -> Terminal).
            if error is CancellationError || (error as? URLError)?.code == .cancelled || Task.isCancelled {
                return .terminal
            }
            return .transient
        }
    }

    /// Atomically apply a successful refresh — unless a competing
    /// transition superseded this refresh while the request was in flight
    /// (stale generation, or the session was killed outright).
    private func applyRefreshSuccess(access: String, refresh: String, generation: Int) -> RefreshResult {
        if refreshGeneration != generation || state == .unauthenticated {
            return .terminal
        }
        tokenStore.saveTokens(accessToken: access, refreshToken: refresh)
        setState(.fullyConnected)
        return .success
    }

    /// Terminal refresh outcome: drop the agent connection, keep the user
    /// identity when still valid. A stale (superseded) refresh must not
    /// mutate anything — e.g. a late 401 landing after
    /// `markIncompatibleServer` would otherwise demote the takeover state.
    private func terminalAgentOnly(generation: Int) -> RefreshResult {
        if refreshGeneration != generation {
            return .terminal
        }
        tokenStore.clear()
        setState(userIdentityStore.isUserLoggedIn() ? .userOnly : .unauthenticated)
        return .terminal
    }

    private func setState(_ newState: AuthState) {
        state = newState
        authStateBroadcaster.emit(newState)
    }

    /// Bare transport with no auth adapter: the refresh call must not flow
    /// back through the token-injecting request path (Android's dedicated
    /// interceptor-free `@Named("refresh")` client).
    private static func defaultRefreshTransport() -> RefreshTransport {
        let client = HTTPClient()
        return { request in try await client.send(request) }
    }
}
