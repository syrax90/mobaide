import Foundation

/// `skip_permissions` and `sandbox` are mutually exclusive (the server rejects
/// the pair with a 422); the UI enforces it before submit.
struct AntigravityOptionsModel: Codable, Equatable, Sendable {
    let model: String?
    let skip_permissions: Bool
    let sandbox: Bool
    let add_dirs: [String]
    let worktree: Bool
    let branch_name: String?

    init(
        model: String? = nil,
        skip_permissions: Bool = false,
        sandbox: Bool = false,
        add_dirs: [String] = [],
        worktree: Bool = false,
        branch_name: String? = nil
    ) {
        self.model = model
        self.skip_permissions = skip_permissions
        self.sandbox = sandbox
        self.add_dirs = add_dirs
        self.worktree = worktree
        self.branch_name = branch_name
    }
}

extension AntigravityOptionsModel {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        skip_permissions = try container.decodeIfPresent(Bool.self, forKey: .skip_permissions) ?? false
        sandbox = try container.decodeIfPresent(Bool.self, forKey: .sandbox) ?? false
        add_dirs = try container.decodeIfPresent([String].self, forKey: .add_dirs) ?? []
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
    }
}
