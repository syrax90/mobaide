import Foundation

/// The per-session socket URL (`ws/session/{id}`, relay-rewritten),
/// re-evaluated on every (re)connect so a refreshed token is picked up.
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
            guard let base = tokenStore.getRelayServerUrl() else { return nil }
            return RelayRouting.agentWebSocketURLString(
                relayServerURL: base,
                path: "ws/session/\(sessionId)",
                agentId: tokenStore.getAgentId(),
                token: token
            )
        }
    }
}
