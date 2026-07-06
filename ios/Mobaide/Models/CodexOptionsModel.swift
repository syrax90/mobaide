import Foundation

struct CodexOptionsModel: Codable, Equatable, Sendable {
    let model: String?
    let sandbox: String
    let approval: String
    /// Nullable on the wire but defaults to "high" when the key is absent;
    /// an explicit JSON null stays nil (three-state, unlike `?? default`).
    let reasoning_effort: String?
    let full_auto: Bool
    let initial_prompt: String?
    let worktree: Bool
    let branch_name: String?

    init(
        model: String? = nil,
        sandbox: String = "workspace-write",
        approval: String = "never",
        reasoning_effort: String? = "high",
        full_auto: Bool = false,
        initial_prompt: String? = nil,
        worktree: Bool = false,
        branch_name: String? = nil
    ) {
        self.model = model
        self.sandbox = sandbox
        self.approval = approval
        self.reasoning_effort = reasoning_effort
        self.full_auto = full_auto
        self.initial_prompt = initial_prompt
        self.worktree = worktree
        self.branch_name = branch_name
    }
}

extension CodexOptionsModel {
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
        initial_prompt = try container.decodeIfPresent(String.self, forKey: .initial_prompt)
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
    }
}
