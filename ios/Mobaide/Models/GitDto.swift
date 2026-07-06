import Foundation

struct GitItem: Codable, Equatable, Sendable {
    static let kindBranchWithWorktree = "branch_with_worktree"
    static let kindBranchOnly = "branch_only"
    static let kindOrphanWorktree = "orphan_worktree"

    let kind: String
    let branch: String?
    let worktree_path: String?
    let is_current: Bool
    let upstream: String?
    let ahead: Int
    let behind: Int
    let gone: Bool
    let locked: Bool
    let locked_reason: String?
    let detached: Bool
    let prunable: Bool

    init(
        kind: String,
        branch: String? = nil,
        worktree_path: String? = nil,
        is_current: Bool = false,
        upstream: String? = nil,
        ahead: Int = 0,
        behind: Int = 0,
        gone: Bool = false,
        locked: Bool = false,
        locked_reason: String? = nil,
        detached: Bool = false,
        prunable: Bool = false
    ) {
        self.kind = kind
        self.branch = branch
        self.worktree_path = worktree_path
        self.is_current = is_current
        self.upstream = upstream
        self.ahead = ahead
        self.behind = behind
        self.gone = gone
        self.locked = locked
        self.locked_reason = locked_reason
        self.detached = detached
        self.prunable = prunable
    }

    var id: String {
        switch kind {
        case Self.kindBranchWithWorktree, Self.kindBranchOnly:
            return "\(kind):\(branch ?? worktree_path ?? "")"
        case Self.kindOrphanWorktree:
            return "\(kind):\(worktree_path ?? "")"
        default:
            return "\(kind):\(branch ?? "")|\(worktree_path ?? "")"
        }
    }

    var isDeletable: Bool {
        !is_current
    }
}

extension GitItem {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        kind = try container.decode(String.self, forKey: .kind)
        branch = try container.decodeIfPresent(String.self, forKey: .branch)
        worktree_path = try container.decodeIfPresent(String.self, forKey: .worktree_path)
        is_current = try container.decodeIfPresent(Bool.self, forKey: .is_current) ?? false
        upstream = try container.decodeIfPresent(String.self, forKey: .upstream)
        ahead = try container.decodeIfPresent(Int.self, forKey: .ahead) ?? 0
        behind = try container.decodeIfPresent(Int.self, forKey: .behind) ?? 0
        gone = try container.decodeIfPresent(Bool.self, forKey: .gone) ?? false
        locked = try container.decodeIfPresent(Bool.self, forKey: .locked) ?? false
        locked_reason = try container.decodeIfPresent(String.self, forKey: .locked_reason)
        detached = try container.decodeIfPresent(Bool.self, forKey: .detached) ?? false
        prunable = try container.decodeIfPresent(Bool.self, forKey: .prunable) ?? false
    }
}

struct GitItemsResponse: Codable, Equatable, Sendable {
    let items: [GitItem]
}

struct GitDeleteItem: Codable, Equatable, Sendable {
    let kind: String
    let branch: String?
    let worktree_path: String?
    let force: Bool

    init(kind: String, branch: String? = nil, worktree_path: String? = nil, force: Bool = false) {
        self.kind = kind
        self.branch = branch
        self.worktree_path = worktree_path
        self.force = force
    }
}

extension GitDeleteItem {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        kind = try container.decode(String.self, forKey: .kind)
        branch = try container.decodeIfPresent(String.self, forKey: .branch)
        worktree_path = try container.decodeIfPresent(String.self, forKey: .worktree_path)
        force = try container.decodeIfPresent(Bool.self, forKey: .force) ?? false
    }
}

struct GitDeleteRequest: Codable, Equatable, Sendable {
    let items: [GitDeleteItem]
}

struct GitDeleteResultItem: Codable, Equatable, Sendable {
    let kind: String
    let target: String
    let ok: Bool
    let error: String?
    let error_detail: String?
}

struct GitDeleteResponse: Codable, Equatable, Sendable {
    let results: [GitDeleteResultItem]
}

struct BusyHolder: Codable, Equatable, Sendable {
    let kind: String
    let session_id: String?
    let project_name: String?
    let cwd: String?
    let worktree_path: String?
}

struct BusyByResponse: Codable, Equatable, Sendable {
    let busy_by: [BusyHolder]
}
