import Foundation

/// Wire values mirror the Kotlin `wireName` mapping. An unknown raw value is
/// preserved verbatim as `.custom` (proto-v7, Android `SessionKindSerializer`
/// parity) — coercing it to `.claude` would silently render Claude's key bar
/// and re-bucket shortcuts for custom agent types (`custom_<12hex>`).
enum SessionKind: Hashable, Sendable, Codable {
    case claude
    case codex
    case antigravity
    case opencode
    case copilot
    case autoScan
    case shell
    /// Any kind string this build does not recognize, held verbatim.
    case custom(String)

    var wireName: String {
        switch self {
        case .claude: return "claude"
        case .codex: return "codex"
        case .antigravity: return "antigravity"
        case .opencode: return "opencode"
        case .copilot: return "copilot"
        case .autoScan: return "auto_scan"
        case .shell: return "shell"
        case .custom(let raw): return raw
        }
    }

    init(wire: String) {
        switch wire {
        case "claude": self = .claude
        case "codex": self = .codex
        case "antigravity": self = .antigravity
        case "opencode": self = .opencode
        case "copilot": self = .copilot
        case "auto_scan": self = .autoScan
        case "shell": self = .shell
        default: self = .custom(wire)
        }
    }

    init(from decoder: any Decoder) throws {
        self.init(wire: try decoder.singleValueContainer().decode(String.self))
    }

    func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(wireName)
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
    /// Display-name snapshot of a custom agent type at session-creation time
    /// (proto-v7); nil for built-in kinds. Survives rename/delete of the type,
    /// so lists keep a meaningful label without a live registry lookup.
    let kind_label: String?
    let attention: String?
    let exit_reason: String?
    /// `"chat"` or `"terminal"`; absent or any other value is the terminal.
    let ui: String?
    /// `"working"` while the agent is mid-turn, `"waiting"` while it is parked
    /// on background work, `"idle"` between turns; nil when the agent cannot
    /// tell (Codex, Antigravity, OpenCode, Copilot and Terminal PTY sessions).
    let activity: String?

    var isChat: Bool { ui == "chat" }

    init(
        session_id: String,
        state: String,
        created_at: String,
        pid: Int?,
        command: [String],
        cwd: String?,
        title: String?,
        project_name: String?,
        kind: SessionKind,
        kind_label: String? = nil,
        attention: String?,
        exit_reason: String?,
        ui: String? = nil,
        activity: String? = nil
    ) {
        self.session_id = session_id
        self.state = state
        self.created_at = created_at
        self.pid = pid
        self.command = command
        self.cwd = cwd
        self.title = title
        self.project_name = project_name
        self.kind = kind
        self.kind_label = kind_label
        self.attention = attention
        self.exit_reason = exit_reason
        self.ui = ui
        self.activity = activity
    }
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
        // Explicit-null/absent kind still falls back to the property default
        // (Kotlin `coerceInputValues` parity); unknown strings are preserved.
        kind = try container.decodeIfPresent(SessionKind.self, forKey: .kind) ?? .claude
        kind_label = try container.decodeIfPresent(String.self, forKey: .kind_label)
        attention = try container.decodeIfPresent(String.self, forKey: .attention)
        exit_reason = try container.decodeIfPresent(String.self, forKey: .exit_reason)
        ui = try container.decodeIfPresent(String.self, forKey: .ui)
        activity = try container.decodeIfPresent(String.self, forKey: .activity)
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
    let opencode_options: OpenCodeOptionsModel?
    let copilot_options: CopilotOptionsModel?
    let worktree: Bool
    let branch_name: String?
    let ui: String?
    let chat_resume_id: String?
    let base_branch: String?

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
        opencode_options: OpenCodeOptionsModel? = nil,
        copilot_options: CopilotOptionsModel? = nil,
        worktree: Bool = false,
        branch_name: String? = nil,
        ui: String? = nil,
        chat_resume_id: String? = nil,
        base_branch: String? = nil
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
        self.opencode_options = opencode_options
        self.copilot_options = copilot_options
        self.worktree = worktree
        self.branch_name = branch_name
        self.ui = ui
        self.chat_resume_id = chat_resume_id
        self.base_branch = base_branch
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
        opencode_options = try container.decodeIfPresent(OpenCodeOptionsModel.self, forKey: .opencode_options)
        copilot_options = try container.decodeIfPresent(CopilotOptionsModel.self, forKey: .copilot_options)
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
        ui = try container.decodeIfPresent(String.self, forKey: .ui)
        chat_resume_id = try container.decodeIfPresent(String.self, forKey: .chat_resume_id)
        base_branch = try container.decodeIfPresent(String.self, forKey: .base_branch)
    }
}

struct ScrollbackResponse: Codable, Equatable, Sendable {
    let session_id: String
    let lines: [String]
    let total: Int
}
