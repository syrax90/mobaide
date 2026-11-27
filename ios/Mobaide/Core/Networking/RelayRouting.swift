import Foundation

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

    /// `{ws|wss}://{host}/{path}` on the relay server URL, the path taken as
    /// given: for the channels the relay hosts itself (`ws/user-events`),
    /// which are never path-rewritten. Scheme is `wss` iff the URL starts
    /// with "https".
    static func webSocketURLString(
        relayServerURL: String,
        path: String,
        token: String
    ) -> String {
        let scheme = relayServerURL.hasPrefix("https") ? "wss" : "ws"
        let host = stripHTTPScheme(relayServerURL)
        return appendToken(to: "\(scheme)://\(host)/\(path)", token: token)
    }

    /// An agent's channel: same host handling, with the path rewritten via
    /// `mapToRelayPath`.
    static func agentWebSocketURLString(
        relayServerURL: String,
        path: String,
        agentId: String?,
        token: String
    ) -> String {
        webSocketURLString(
            relayServerURL: relayServerURL,
            path: mapToRelayPath(path, agentId: agentId ?? ""),
            token: token
        )
    }

    /// `?token=` normally, `&token=` when the URL already carries a query.
    static func appendToken(to url: String, token: String) -> String {
        appendQueryItem(to: url, name: "token", value: token)
    }

    /// Append one query item, picking `?` or `&` by whether the URL already
    /// carries a query. Values are interpolated verbatim — every caller passes
    /// an already-URL-safe token (JWT, UUID).
    static func appendQueryItem(to url: String, name: String, value: String) -> String {
        let separator = url.contains("?") ? "&" : "?"
        return "\(url)\(separator)\(name)=\(value)"
    }

    /// Agent REST base: `{relay}/proxy/{agentId}`.
    static func agentRESTBaseURL(relayServerURL: String?, agentId: String?) -> String? {
        guard let relayServerURL, let agentId else { return nil }
        return "\(trimTrailingSlashes(relayServerURL))/proxy/\(agentId)"
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
