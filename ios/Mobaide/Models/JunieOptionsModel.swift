import Foundation

struct JunieOptionsModel: Codable, Equatable, Sendable {
    let plan_mode: Bool
    let brave: Bool
    let worktree: Bool
    let branch_name: String?
    let base_branch: String?

    init(
        plan_mode: Bool = false,
        brave: Bool = false,
        worktree: Bool = false,
        branch_name: String? = nil,
        base_branch: String? = nil
    ) {
        self.plan_mode = plan_mode
        self.brave = brave
        self.worktree = worktree
        self.branch_name = branch_name
        self.base_branch = base_branch
    }
}

extension JunieOptionsModel {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        plan_mode = try container.decodeIfPresent(Bool.self, forKey: .plan_mode) ?? false
        brave = try container.decodeIfPresent(Bool.self, forKey: .brave) ?? false
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
        base_branch = try container.decodeIfPresent(String.self, forKey: .base_branch)
    }
}
