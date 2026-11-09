import Foundation

/// The per-session socket URL (`ws/session/{id}`, relay-rewritten in relay
/// mode), re-evaluated on every (re)connect so a refreshed token is picked up.
/// Shared by terminal and chat sessions, which use the same socket.
enum SessionSocketURL {
    static func provider(
        sessionId: String,
        tokenStore: TokenStore,
        sessionManager: SessionManager
    ) -> @Sendable () async throws -> String? {
        {
            guard await sessionManager.isLoggedIn() else { return nil }
            if tokenStore.isAccessTokenExpiringSoon() {
                if await sessionManager.refresh() == .terminal { return nil }
            }
            guard let token = tokenStore.getAccessToken() else { return nil }
            let mode = tokenStore.getConnectionMode()
            let base: String?
            switch mode {
            case .direct:
                base = tokenStore.getAgentUrl()
            case .relay:
                base = tokenStore.getRelayServerUrl()
            }
            guard let base else { return nil }
            return RelayRouting.webSocketURLString(
                baseURL: base,
                path: "ws/session/\(sessionId)",
                mode: mode,
                agentId: tokenStore.getAgentId(),
                token: token
            )
        }
    }
}
