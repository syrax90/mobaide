import Foundation

/// Every field has a default, so the whole object may arrive absent or
/// partial on the wire; all five decode via `decodeIfPresent ?? default`.
struct AutoScanFilterDto: Codable, Equatable, Sendable {
    let state_names: [String]
    let label_names: [String]
    let active_state: String
    let done_state: String
    let removal_label: String

    init(
        state_names: [String] = ["Proposals"],
        label_names: [String] = ["AutoPipeline"],
        active_state: String = "In Progress",
        done_state: String = "Done",
        removal_label: String = "AutoPipeline"
    ) {
        self.state_names = state_names
        self.label_names = label_names
        self.active_state = active_state
        self.done_state = done_state
        self.removal_label = removal_label
    }
}

extension AutoScanFilterDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        state_names = try container.decodeIfPresent([String].self, forKey: .state_names) ?? ["Proposals"]
        label_names = try container.decodeIfPresent([String].self, forKey: .label_names) ?? ["AutoPipeline"]
        active_state = try container.decodeIfPresent(String.self, forKey: .active_state) ?? "In Progress"
        done_state = try container.decodeIfPresent(String.self, forKey: .done_state) ?? "Done"
        removal_label = try container.decodeIfPresent(String.self, forKey: .removal_label) ?? "AutoPipeline"
    }
}

struct LinearLabelDto: Codable, Equatable, Sendable {
    let name: String
    let color: String

    init(name: String, color: String = "") {
        self.name = name
        self.color = color
    }
}

extension LinearLabelDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        color = try container.decodeIfPresent(String.self, forKey: .color) ?? ""
    }
}

struct LinearStateDto: Codable, Equatable, Sendable {
    let name: String
    let type: String

    init(name: String, type: String = "") {
        self.name = name
        self.type = type
    }
}

extension LinearStateDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        type = try container.decodeIfPresent(String.self, forKey: .type) ?? ""
    }
}

/// The GitHub filter of Auto Scan: an open issue with any of `labels` (1–20
/// names) is picked up; a nil `assignee` takes any assignee and `@me` the
/// signed-in `gh` account; `active_label` is added when work starts.
struct GitHubScanFilterDto: Codable, Equatable, Sendable {
    let labels: [String]
    let assignee: String?
    let active_label: String?

    init(labels: [String], assignee: String? = nil, active_label: String? = nil) {
        self.labels = labels
        self.assignee = assignee
        self.active_label = active_label
    }
}

extension GitHubScanFilterDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        labels = try container.decodeIfPresent([String].self, forKey: .labels) ?? []
        assignee = try container.decodeIfPresent(String.self, forKey: .assignee)
        active_label = try container.decodeIfPresent(String.self, forKey: .active_label)
    }
}

/// `WorkspaceInfo.auto_scan`: the last Auto Scan choices of a workspace. Each
/// field is nil until a scan start sets it; a nil filter means nobody chose
/// one, so the wizard starts with empty fields. Absent on older agents.
struct AutoScanSettingsDto: Codable, Equatable, Sendable {
    let source: String?
    let cli: String?
    let linear_filter: AutoScanFilterDto?
    let github_filter: GitHubScanFilterDto?

    init(
        source: String? = nil,
        cli: String? = nil,
        linear_filter: AutoScanFilterDto? = nil,
        github_filter: GitHubScanFilterDto? = nil
    ) {
        self.source = source
        self.cli = cli
        self.linear_filter = linear_filter
        self.github_filter = github_filter
    }
}
