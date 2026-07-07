import Foundation

/// Concrete `AgentTokenProviding` behind `AgentAPI`, porting Android
/// `AuthInterceptor` semantics: no token while not fully connected
/// (AgentAPI synthesizes the local 401), a pre-emptive single-flight
/// refresh when the access token is expiring soon (`justRefreshed`
/// suppresses the reactive 401 retry), and proceed-with-stale-token on a
/// transient refresh failure.
struct SessionAgentTokenProvider: AgentTokenProviding {
    let tokenStore: TokenStore
    let sessionManager: SessionManager

    func validToken() async -> AgentToken? {
        guard await sessionManager.isLoggedIn() else { return nil }
        if tokenStore.isAccessTokenExpiringSoon() {
            switch await sessionManager.refresh() {
            case .success:
                guard let token = tokenStore.getAccessToken() else { return nil }
                return AgentToken(value: token, justRefreshed: true)
            case .terminal:
                return nil
            case .transient:
                break
            }
        }
        guard let token = tokenStore.getAccessToken() else { return nil }
        return AgentToken(value: token)
    }

    func refreshedToken() async -> String? {
        switch await sessionManager.refresh() {
        case .success:
            return tokenStore.getAccessToken()
        case .terminal, .transient:
            return nil
        }
    }
}

/// Dashboard/user JWT provider for central-server calls and user-scoped
/// WS channels.
struct SessionUserTokenProvider: UserTokenProviding {
    let userIdentityStore: UserIdentityStore

    func userToken() async -> String? {
        userIdentityStore.getUserAccessToken()
    }
}

/// `SessionInvalidating` behind `CentralAPI`, porting Android
/// `CentralAuthInterceptor`: a central 401 is terminal (no dashboard
/// refresh token exists).
struct SessionInvalidatorAdapter: SessionInvalidating {
    let sessionManager: SessionManager

    func invalidateUserSession() async {
        await sessionManager.invalidateUserSession(.tokenRejected)
    }
}
