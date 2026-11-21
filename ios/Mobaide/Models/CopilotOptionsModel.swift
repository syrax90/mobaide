import Foundation

struct CopilotOptionsModel: Codable, Equatable, Sendable {
    let allow_all: Bool
    /// Copilot's `--sandbox`; false sends no flag, so a sandbox saved in Copilot stays on.
    let sandbox: Bool
    let worktree: Bool
    let branch_name: String?
    let base_branch: String?

    init(
        allow_all: Bool = false,
        sandbox: Bool = false,
        worktree: Bool = false,
        branch_name: String? = nil,
        base_branch: String? = nil
    ) {
        self.allow_all = allow_all
        self.sandbox = sandbox
        self.worktree = worktree
        self.branch_name = branch_name
        self.base_branch = base_branch
    }
}

extension CopilotOptionsModel {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        allow_all = try container.decodeIfPresent(Bool.self, forKey: .allow_all) ?? false
        sandbox = try container.decodeIfPresent(Bool.self, forKey: .sandbox) ?? false
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
        base_branch = try container.decodeIfPresent(String.self, forKey: .base_branch)
    }
}
