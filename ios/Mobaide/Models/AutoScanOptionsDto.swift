import Foundation

// The options an Auto Scan keeps per agent kind (PROTOCOL.md "Agent options"):
// the session options minus the worktree, branch, prompt and sign-in fields.
// Every field decodes with the agent's default when absent, so a partial object
// still reads.

struct ClaudeScanOptionsDto: Codable, Equatable, Sendable {
    var plan_mode: Bool
    var no_memory: Bool
    var skip_permissions: Bool
    var model: String?
    var effort: String?
    var fast_mode: Bool
    var ultracode: Bool
    var append_system_prompt: String?
    var debug: Bool
    var focus_mode: Bool?
    /// Terminal UI only: a chat scan ignores it.
    var code_mode: Bool

    init(
        plan_mode: Bool = false,
        no_memory: Bool = false,
        skip_permissions: Bool = true,
        model: String? = nil,
        effort: String? = nil,
        fast_mode: Bool = false,
        ultracode: Bool = false,
        append_system_prompt: String? = nil,
        debug: Bool = false,
        focus_mode: Bool? = nil,
        code_mode: Bool = false
    ) {
        self.plan_mode = plan_mode
        self.no_memory = no_memory
        self.skip_permissions = skip_permissions
        self.model = model
        self.effort = effort
        self.fast_mode = fast_mode
        self.ultracode = ultracode
        self.append_system_prompt = append_system_prompt
        self.debug = debug
        self.focus_mode = focus_mode
        self.code_mode = code_mode
    }
}

extension ClaudeScanOptionsDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        plan_mode = try container.decodeIfPresent(Bool.self, forKey: .plan_mode) ?? false
        no_memory = try container.decodeIfPresent(Bool.self, forKey: .no_memory) ?? false
        skip_permissions = try container.decodeIfPresent(Bool.self, forKey: .skip_permissions) ?? true
        model = try container.decodeIfPresent(String.self, forKey: .model)
        effort = try container.decodeIfPresent(String.self, forKey: .effort)
        fast_mode = try container.decodeIfPresent(Bool.self, forKey: .fast_mode) ?? false
        ultracode = try container.decodeIfPresent(Bool.self, forKey: .ultracode) ?? false
        append_system_prompt = try container.decodeIfPresent(String.self, forKey: .append_system_prompt)
        debug = try container.decodeIfPresent(Bool.self, forKey: .debug) ?? false
        focus_mode = try container.decodeIfPresent(Bool.self, forKey: .focus_mode)
        code_mode = try container.decodeIfPresent(Bool.self, forKey: .code_mode) ?? false
    }
}

/// Defaults are Auto Scan's (Full access), not a Codex session's: a scan runs
/// unattended.
struct CodexScanOptionsDto: Codable, Equatable, Sendable {
    var model: String?
    var sandbox: String
    var approval: String
    /// Three-state, as in `CodexOptionsModel`: absent = "high", an explicit
    /// null = no reasoning-effort override.
    var reasoning_effort: String?
    var full_auto: Bool

    init(
        model: String? = nil,
        sandbox: String = "workspace-write",
        approval: String = "never",
        reasoning_effort: String? = "high",
        full_auto: Bool = true
    ) {
        self.model = model
        self.sandbox = sandbox
        self.approval = approval
        self.reasoning_effort = reasoning_effort
        self.full_auto = full_auto
    }

    /// The autonomy preset the options match; nil for a hand-picked pair.
    var preset: CodexAutonomyPreset? {
        CodexAutonomyPreset.matching(sandbox: sandbox, approval: approval, fullAuto: full_auto)
    }

    mutating func apply(_ preset: CodexAutonomyPreset) {
        sandbox = preset.sandbox
        approval = preset.approval
        full_auto = preset == .fullAccess
    }
}

extension CodexScanOptionsDto {
    private enum CodingKeys: String, CodingKey {
        case model, sandbox, approval, reasoning_effort, full_auto
    }

    func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(model, forKey: .model)
        try container.encode(sandbox, forKey: .sandbox)
        try container.encode(approval, forKey: .approval)
        try container.encode(reasoning_effort, forKey: .reasoning_effort)
        try container.encode(full_auto, forKey: .full_auto)
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        sandbox = try container.decodeIfPresent(String.self, forKey: .sandbox) ?? "workspace-write"
        approval = try container.decodeIfPresent(String.self, forKey: .approval) ?? "never"
        reasoning_effort = container.contains(.reasoning_effort)
            ? try container.decodeIfPresent(String.self, forKey: .reasoning_effort)
            : "high"
        full_auto = try container.decodeIfPresent(Bool.self, forKey: .full_auto) ?? false
    }
}

/// `skip_permissions` and `sandbox` are mutually exclusive (the agent answers
/// 422 for the pair); the setters below keep them apart.
struct AntigravityScanOptionsDto: Codable, Equatable, Sendable {
    var model: String?
    var skip_permissions: Bool
    var sandbox: Bool
    var add_dirs: [String]

    init(model: String? = nil, skip_permissions: Bool = true, sandbox: Bool = false, add_dirs: [String] = []) {
        self.model = model
        self.skip_permissions = skip_permissions
        self.sandbox = sandbox && !skip_permissions
        self.add_dirs = add_dirs
    }
}

extension AntigravityScanOptionsDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        skip_permissions = try container.decodeIfPresent(Bool.self, forKey: .skip_permissions) ?? false
        sandbox = try container.decodeIfPresent(Bool.self, forKey: .sandbox) ?? false
        add_dirs = try container.decodeIfPresent([String].self, forKey: .add_dirs) ?? []
    }
}

/// `code_mode` is on by default, like OpenCode 2 itself.
struct OpenCodeScanOptionsDto: Codable, Equatable, Sendable {
    var auto_approve: Bool
    var code_mode: Bool

    init(auto_approve: Bool = true, code_mode: Bool = true) {
        self.auto_approve = auto_approve
        self.code_mode = code_mode
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        auto_approve = try container.decodeIfPresent(Bool.self, forKey: .auto_approve) ?? false
        code_mode = try container.decodeIfPresent(Bool.self, forKey: .code_mode) ?? true
    }
}

struct CopilotScanOptionsDto: Codable, Equatable, Sendable {
    var allow_all: Bool

    init(allow_all: Bool = true) { self.allow_all = allow_all }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        allow_all = try container.decodeIfPresent(Bool.self, forKey: .allow_all) ?? false
    }
}

struct CursorScanOptionsDto: Codable, Equatable, Sendable {
    var force: Bool

    init(force: Bool = true) { self.force = force }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        force = try container.decodeIfPresent(Bool.self, forKey: .force) ?? false
    }
}

struct JunieScanOptionsDto: Codable, Equatable, Sendable {
    var plan_mode: Bool
    var brave: Bool

    init(plan_mode: Bool = false, brave: Bool = true) {
        self.plan_mode = plan_mode
        self.brave = brave
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        plan_mode = try container.decodeIfPresent(Bool.self, forKey: .plan_mode) ?? false
        brave = try container.decodeIfPresent(Bool.self, forKey: .brave) ?? false
    }
}

struct PiScanOptionsDto: Codable, Equatable, Sendable {
    var code_mode: Bool

    init(code_mode: Bool = false) { self.code_mode = code_mode }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        code_mode = try container.decodeIfPresent(Bool.self, forKey: .code_mode) ?? false
    }
}
