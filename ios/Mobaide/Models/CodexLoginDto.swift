import Foundation

/// `api_key` is stdin-piped to `codex login --with-api-key` on the agent host
/// and never persisted by app or agent.
struct CodexLoginRequest: Codable, Equatable, Sendable {
    let api_key: String
}

struct CodexLoginResponse: Codable, Equatable, Sendable {
    let authenticated: Bool
}
