import Foundation

struct CodexOptionsModel: Codable, Equatable, Sendable {
    let model: String?
    let sandbox: String
    let approval: String
    /// Three-state on the wire in both directions: an absent key means the
    /// server default ("high"), an explicit JSON null means "no
    /// reasoning-effort override" (the agent drops the CLI flag), and a value
    /// overrides. Decode keeps null distinct from absent; encode emits an
    /// explicit null for nil (the synthesized encoder would omit it and
    /// silently re-enable "high").
    let reasoning_effort: String?
    let full_auto: Bool
    /// Codex `--approve-for-me`; never with `full_auto` (the agent answers 422).
    let auto_review: Bool
    let initial_prompt: String?
    let worktree: Bool
    let branch_name: String?
    let base_branch: String?

    init(
        model: String? = nil,
        sandbox: String = "workspace-write",
        approval: String = "never",
        reasoning_effort: String? = "high",
        full_auto: Bool = false,
        auto_review: Bool = false,
        initial_prompt: String? = nil,
        worktree: Bool = false,
        branch_name: String? = nil,
        base_branch: String? = nil
    ) {
        self.model = model
        self.sandbox = sandbox
        self.approval = approval
        self.reasoning_effort = reasoning_effort
        self.full_auto = full_auto
        self.auto_review = auto_review
        self.initial_prompt = initial_prompt
        self.worktree = worktree
        self.branch_name = branch_name
        self.base_branch = base_branch
    }
}

extension CodexOptionsModel {
    private enum CodingKeys: String, CodingKey {
        case model
        case sandbox
        case approval
        case reasoning_effort
        case full_auto
        case auto_review
        case initial_prompt
        case worktree
        case branch_name
        case base_branch
    }

    func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(model, forKey: .model)
        try container.encode(sandbox, forKey: .sandbox)
        try container.encode(approval, forKey: .approval)
        try container.encode(reasoning_effort, forKey: .reasoning_effort)
        try container.encode(full_auto, forKey: .full_auto)
        try container.encode(auto_review, forKey: .auto_review)
        try container.encodeIfPresent(initial_prompt, forKey: .initial_prompt)
        try container.encode(worktree, forKey: .worktree)
        try container.encodeIfPresent(branch_name, forKey: .branch_name)
        try container.encodeIfPresent(base_branch, forKey: .base_branch)
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        sandbox = try container.decodeIfPresent(String.self, forKey: .sandbox) ?? "workspace-write"
        approval = try container.decodeIfPresent(String.self, forKey: .approval) ?? "never"
        if container.contains(.reasoning_effort) {
            reasoning_effort = try container.decodeIfPresent(String.self, forKey: .reasoning_effort)
        } else {
            reasoning_effort = "high"
        }
        full_auto = try container.decodeIfPresent(Bool.self, forKey: .full_auto) ?? false
        auto_review = try container.decodeIfPresent(Bool.self, forKey: .auto_review) ?? false
        initial_prompt = try container.decodeIfPresent(String.self, forKey: .initial_prompt)
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
        base_branch = try container.decodeIfPresent(String.self, forKey: .base_branch)
    }
}
