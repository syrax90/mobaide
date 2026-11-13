import Foundation

struct PiOptionsModel: Codable, Equatable, Sendable {
    let worktree: Bool
    let branch_name: String?
    let base_branch: String?

    init(
        worktree: Bool = false,
        branch_name: String? = nil,
        base_branch: String? = nil
    ) {
        self.worktree = worktree
        self.branch_name = branch_name
        self.base_branch = base_branch
    }
}

extension PiOptionsModel {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
        base_branch = try container.decodeIfPresent(String.self, forKey: .base_branch)
    }
}
