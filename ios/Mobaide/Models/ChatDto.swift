import Foundation

/// `GET /api/chat/{kind}/sessions`: the agent's stored conversations, newest first.
struct ChatStoredSessionsResponse: Codable, Equatable, Sendable {
    let sessions: [ChatStoredSession]
    let next_cursor: String?

    init(sessions: [ChatStoredSession], next_cursor: String? = nil) {
        self.sessions = sessions
        self.next_cursor = next_cursor
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        sessions = try container.decodeIfPresent([ChatStoredSession].self, forKey: .sessions) ?? []
        next_cursor = try container.decodeIfPresent(String.self, forKey: .next_cursor)
    }
}

struct ChatStoredSession: Codable, Equatable, Sendable, Identifiable {
    let session_id: String
    let cwd: String?
    let title: String?
    let updated_at: String?
    /// The Mobaide session that already shows this conversation.
    let open_session_id: String?

    var id: String { session_id }
}
