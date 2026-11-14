import Foundation

struct CursorOptionsModel: Codable, Equatable, Sendable {
    let force: Bool
    let worktree: Bool
    let branch_name: String?
    let base_branch: String?

    init(
        force: Bool = false,
        worktree: Bool = false,
        branch_name: String? = nil,
        base_branch: String? = nil
    ) {
        self.force = force
        self.worktree = worktree
        self.branch_name = branch_name
        self.base_branch = base_branch
    }
}

extension CursorOptionsModel {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        force = try container.decodeIfPresent(Bool.self, forKey: .force) ?? false
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
        base_branch = try container.decodeIfPresent(String.self, forKey: .base_branch)
    }
}
