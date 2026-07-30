import Foundation

struct ClaudeOptionsModel: Codable, Equatable, Sendable {
    let plan_mode: Bool
    let no_memory: Bool
    let skip_permissions: Bool
    let initial_prompt: String?
    let worktree: Bool
    let branch_name: String?
    let identifier: String?
    let title: String?
    let model: String?
    let effort: String?
    let fast_mode: Bool
    let ultracode: Bool
    let append_system_prompt: String?

    init(
        plan_mode: Bool = false,
        no_memory: Bool = false,
        // Kept true, matching the server's wire-absence default — the mobile
        // UI's own toggle (CreateSessionModel.skipPermissions) is what starts
        // off; see the matching comment on Android's ClaudeOptionsModel.kt.
        skip_permissions: Bool = true,
        initial_prompt: String? = nil,
        worktree: Bool = false,
        branch_name: String? = nil,
        identifier: String? = nil,
        title: String? = nil,
        model: String? = nil,
        effort: String? = nil,
        fast_mode: Bool = false,
        ultracode: Bool = false,
        append_system_prompt: String? = nil
    ) {
        self.plan_mode = plan_mode
        self.no_memory = no_memory
        self.skip_permissions = skip_permissions
        self.initial_prompt = initial_prompt
        self.worktree = worktree
        self.branch_name = branch_name
        self.identifier = identifier
        self.title = title
        self.model = model
        self.effort = effort
        self.fast_mode = fast_mode
        self.ultracode = ultracode
        self.append_system_prompt = append_system_prompt
    }
}

extension ClaudeOptionsModel {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        plan_mode = try container.decodeIfPresent(Bool.self, forKey: .plan_mode) ?? false
        no_memory = try container.decodeIfPresent(Bool.self, forKey: .no_memory) ?? false
        skip_permissions = try container.decodeIfPresent(Bool.self, forKey: .skip_permissions) ?? true
        initial_prompt = try container.decodeIfPresent(String.self, forKey: .initial_prompt)
        worktree = try container.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        branch_name = try container.decodeIfPresent(String.self, forKey: .branch_name)
        identifier = try container.decodeIfPresent(String.self, forKey: .identifier)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        effort = try container.decodeIfPresent(String.self, forKey: .effort)
        fast_mode = try container.decodeIfPresent(Bool.self, forKey: .fast_mode) ?? false
        ultracode = try container.decodeIfPresent(Bool.self, forKey: .ultracode) ?? false
        append_system_prompt = try container.decodeIfPresent(String.self, forKey: .append_system_prompt)
    }
}
