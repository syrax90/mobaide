import Foundation

/// `GET /api/chat/{kind}/sessions`: the agent's stored conversations, newest first.
struct ChatStoredSessionsResponse: Codable, Equatable, Sendable {
    let sessions: [ChatStoredSession]
    let next_cursor: String?
    /// The agent supports `DELETE /api/chat/{kind}/sessions/{id}`; missing on an older agent.
    var can_delete: Bool? = nil

    init(sessions: [ChatStoredSession], next_cursor: String? = nil, can_delete: Bool? = nil) {
        self.sessions = sessions
        self.next_cursor = next_cursor
        self.can_delete = can_delete
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        sessions = try container.decodeIfPresent([ChatStoredSession].self, forKey: .sessions) ?? []
        next_cursor = try container.decodeIfPresent(String.self, forKey: .next_cursor)
        can_delete = try container.decodeIfPresent(Bool.self, forKey: .can_delete)
    }
}

struct ChatStoredSession: Codable, Equatable, Sendable, Identifiable {
    let session_id: String
    let cwd: String?
    let title: String?
    let updated_at: String?
    /// The Mobaide session that already shows this conversation.
    let open_session_id: String?
    /// Another app on the PC has it open (Antigravity): sending from both can
    /// overwrite messages.
    var open_elsewhere: Bool? = nil

    var id: String { session_id }

    /// Delete is offered only when the agent supports it and nothing has the session open.
    func canDelete(agentCanDelete: Bool) -> Bool {
        agentCanDelete && open_session_id == nil && open_elsewhere != true
    }
}
