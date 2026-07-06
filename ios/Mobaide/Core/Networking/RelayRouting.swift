import Foundation

enum ConnectionMode: String, Sendable {
    case direct
    case relay
}

/// URL construction ported from Android `WebSocketManager` and
/// `TokenStore.getEffectiveBaseUrl()`. All pure string manipulation — the
/// token is appended raw (never percent-encoded), matching the Kotlin
/// concatenation byte for byte.
enum RelayRouting {
    static func mapToRelayPath(_ path: String, agentId: String) -> String {
        if path.hasPrefix("ws/session/") {
            let sessionId = String(path.dropFirst("ws/session/".count))
            return "relay/ws/session/\(agentId)/\(sessionId)"
        }
        if path == "ws/events" {
            return "relay/ws/events/\(agentId)"
        }
        if path == "ws/sessions" {
            return "relay/ws/sessions/\(agentId)"
        }
        if path == "ws/autoscan" {
            return "relay/ws/autoscan/\(agentId)"
        }
        return "relay/\(path)/\(agentId)"
    }

    /// DIRECT: `{ws|wss}://{host}/{path}`. RELAY: same host handling on the
    /// relay server URL with the path rewritten via `mapToRelayPath`.
    /// Scheme is `wss` iff the base URL starts with "https". User-scoped
    /// channels (`ws/user-events`) pass `.direct` with the relay base URL —
    /// they are never path-rewritten.
    static func webSocketURLString(
        baseURL: String,
        path: String,
        mode: ConnectionMode,
        agentId: String? = nil,
        token: String
    ) -> String {
        let scheme = baseURL.hasPrefix("https") ? "wss" : "ws"
        let host = stripHTTPScheme(baseURL)
        let effectivePath: String
        switch mode {
        case .direct:
            effectivePath = path
        case .relay:
            effectivePath = mapToRelayPath(path, agentId: agentId ?? "")
        }
        return appendToken(to: "\(scheme)://\(host)/\(effectivePath)", token: token)
    }

    /// `?token=` normally, `&token=` when the URL already carries a query.
    static func appendToken(to url: String, token: String) -> String {
        let separator = url.contains("?") ? "&" : "?"
        return "\(url)\(separator)token=\(token)"
    }

    /// Agent REST base: DIRECT → the agent URL; RELAY → `{relay}/proxy/{agentId}`.
    static func agentRESTBaseURL(
        mode: ConnectionMode,
        agentURL: String?,
        relayServerURL: String?,
        agentId: String?
    ) -> String? {
        switch mode {
        case .direct:
            return agentURL
        case .relay:
            guard let relayServerURL, let agentId else { return nil }
            return "\(trimTrailingSlashes(relayServerURL))/proxy/\(agentId)"
        }
    }

    private static func stripHTTPScheme(_ base: String) -> String {
        var host = base
        if host.hasPrefix("https://") {
            host = String(host.dropFirst("https://".count))
        } else if host.hasPrefix("http://") {
            host = String(host.dropFirst("http://".count))
        }
        return trimTrailingSlashes(host)
    }

    static func trimTrailingSlashes(_ url: String) -> String {
        var trimmed = url
        while trimmed.hasSuffix("/") {
            trimmed.removeLast()
        }
        return trimmed
    }
}
