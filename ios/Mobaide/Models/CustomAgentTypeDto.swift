import Foundation

/// Account-scoped custom AI agent type (proto-v7). `id` is 12 lowercase hex
/// chars; the session-kind wire string for this type is `"custom_" + id`.
struct CustomAgentTypeDto: Codable, Equatable, Sendable, Identifiable {
    let id: String
    let name: String
    let command: String
    let created_at: String
    let updated_at: String
}

struct CreateCustomAgentTypeRequest: Codable, Equatable, Sendable {
    let name: String
    let command: String
}

/// PATCH body: omitted fields stay unchanged server-side. The synthesized
/// encoder drops nil fields (`encodeIfPresent`) — `{"name": null}`, blank
/// values, and `{}` are all rejected with 422; there is no
/// empty-string-clears form (unlike shortcut rename).
struct UpdateCustomAgentTypeRequest: Codable, Equatable, Sendable {
    let name: String?
    let command: String?

    init(name: String? = nil, command: String? = nil) {
        self.name = name
        self.command = command
    }
}

/// Payload of `custom_type.deleted` — the key is `id`, not `type_id`.
struct CustomTypeDeletedPayload: Codable, Equatable, Sendable {
    let id: String
}

/// The account's order of the create-session mode picker's cards — the body
/// of `GET`/`PUT /api/session-mode-order` and of the
/// `session_mode_order.updated` push. `order` lists mode ids top to bottom
/// (`claude`, `codex`, `antigravity`, `opencode`, `copilot`, `cursor`, `auto_scan`, `shell`,
/// `custom_<id>`); empty means the default order. `version` grows on every change, so an
/// older snapshot is dropped.
struct SessionModeOrderDto: Codable, Equatable, Sendable {
    let order: [String]
    let version: Int
    let updated_at: String?

    static let empty = SessionModeOrderDto(order: [], version: 0, updated_at: nil)
}

/// PUT body: replaces the whole order.
struct UpdateSessionModeOrderRequest: Codable, Equatable, Sendable {
    let order: [String]
}
