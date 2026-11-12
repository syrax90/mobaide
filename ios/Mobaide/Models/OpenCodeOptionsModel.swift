import Foundation

struct OpenCodeOptionsModel: Codable, Equatable, Sendable {
    let auto_approve: Bool
    let worktree: Bool
    let branch_name: String?
    let base_branch: String?

    init(
        auto_approve: Bool = false,
        worktree: Bool = false,
        branch_name: String? = nil,
        base_branch: String? = nil
    ) {
        self.auto_approve = auto_approve
        self.worktree = worktree
        self.branch_name = branch_name
        self.base_branch = base_branch
    }
}

extension OpenCodeOptionsModel {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        auto_approve = try container.decodeIfPresent(Bool.self, forKey: .auto_approve) ?? false
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
        base_branch = try container.decodeIfPresent(String.self, forKey: .base_branch)
    }
}
