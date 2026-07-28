import Foundation

/// `kind` is deliberately a raw `String`, not `SessionKind`: the wire kind
/// vocabulary is open (`custom_<id>` per-type buckets, future kinds), and the
/// filter `dto.kind == kind.wireName` must compare the verbatim wire string —
/// shortcuts of a kind this build can't render stay invisible, never
/// re-bucketed.
struct ShortcutDto: Codable, Equatable, Sendable {
    let shortcut_id: String
    let kind: String
    let chord_token: String
    let name: String?
    let position: Int
    let created_at: String
    let updated_at: String
    let version: Int
}

struct CreateShortcutRequest: Codable, Equatable, Sendable {
    let kind: String
    let chord_token: String
    let name: String?

    init(kind: String, chord_token: String, name: String? = nil) {
        self.kind = kind
        self.chord_token = chord_token
        self.name = name
    }
}

/// `name` is required by the server (`{}` → 422); the empty string is the
/// wire sentinel for "clear the name" (stored as NULL).
struct UpdateShortcutRequest: Codable, Equatable, Sendable {
    let name: String?

    init(name: String?) {
        self.name = name
    }
}

struct ShortcutDeletedPayload: Codable, Equatable, Sendable {
    let shortcut_id: String
    let version: Int
    let updated_at: String
}
