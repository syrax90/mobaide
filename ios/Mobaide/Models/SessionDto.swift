import Foundation

/// Wire values mirror the Kotlin `@SerialName` mapping. An unknown raw value
/// coerces to `.claude` (kotlinx `coerceInputValues` parity) instead of throwing,
/// so a newer server's session kinds don't break decoding of session snapshots.
enum SessionKind: String, Codable, Equatable, Sendable, CaseIterable {
    case claude
    case codex
    case antigravity
    case autoScan = "auto_scan"
    case shell
}

extension SessionKind {
    init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = SessionKind(rawValue: raw) ?? .claude
    }
}

struct SessionResponse: Codable, Equatable, Sendable {
    let session_id: String
    let state: String
    let created_at: String
    let pid: Int?
    let command: [String]
    let cwd: String?
    let title: String?
    let project_name: String?
    let kind: SessionKind
    let attention: String?
    let exit_reason: String?
}

extension SessionResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        session_id = try container.decode(String.self, forKey: .session_id)
        state = try container.decode(String.self, forKey: .state)
        created_at = try container.decode(String.self, forKey: .created_at)
        pid = try container.decodeIfPresent(Int.self, forKey: .pid)
        command = try container.decodeIfPresent([String].self, forKey: .command) ?? []
        cwd = try container.decodeIfPresent(String.self, forKey: .cwd)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        project_name = try container.decodeIfPresent(String.self, forKey: .project_name)
        kind = try container.decodeIfPresent(SessionKind.self, forKey: .kind) ?? .claude
        attention = try container.decodeIfPresent(String.self, forKey: .attention)
        exit_reason = try container.decodeIfPresent(String.self, forKey: .exit_reason)
    }
}

struct CreateSessionRequest: Codable, Equatable, Sendable {
    let command: [String]?
    let cwd: String?
    let env: [String: String]?
    let rows: Int
    let cols: Int
    let project_name: String?
    let cli: String?
    let claude_options: ClaudeOptionsModel?
    let codex_options: CodexOptionsModel?
    let antigravity_options: AntigravityOptionsModel?
    let worktree: Bool
    let branch_name: String?

    init(
        command: [String]? = nil,
        cwd: String? = nil,
        env: [String: String]? = nil,
        rows: Int = 24,
        cols: Int = 80,
        project_name: String? = nil,
        cli: String? = nil,
        claude_options: ClaudeOptionsModel? = nil,
        codex_options: CodexOptionsModel? = nil,
        antigravity_options: AntigravityOptionsModel? = nil,
        worktree: Bool = false,
        branch_name: String? = nil
    ) {
        self.command = command
        self.cwd = cwd
        self.env = env
        self.rows = rows
        self.cols = cols
        self.project_name = project_name
        self.cli = cli
        self.claude_options = claude_options
        self.codex_options = codex_options
        self.antigravity_options = antigravity_options
        self.worktree = worktree
        self.branch_name = branch_name
    }
}

extension CreateSessionRequest {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        command = try container.decodeIfPresent([String].self, forKey: .command)
        cwd = try container.decodeIfPresent(String.self, forKey: .cwd)
        env = try container.decodeIfPresent([String: String].self, forKey: .env)
        rows = try container.decodeIfPresent(Int.self, forKey: .rows) ?? 24
        cols = try container.decodeIfPresent(Int.self, forKey: .cols) ?? 80
        project_name = try container.decodeIfPresent(String.self, forKey: .project_name)
        cli = try container.decodeIfPresent(String.self, forKey: .cli)
        claude_options = try container.decodeIfPresent(ClaudeOptionsModel.self, forKey: .claude_options)
        codex_options = try container.decodeIfPresent(CodexOptionsModel.self, forKey: .codex_options)
        antigravity_options = try container.decodeIfPresent(AntigravityOptionsModel.self, forKey: .antigravity_options)
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
    }
}

struct ScrollbackResponse: Codable, Equatable, Sendable {
    let session_id: String
    let lines: [String]
    let total: Int
}
