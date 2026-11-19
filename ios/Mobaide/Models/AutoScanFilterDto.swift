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
/// signed-in `gh` account; `active_label` is added when work starts. A non-empty
/// `issue_types` takes only issues of those GitHub issue types. A template's
/// filter is a draft: `labels` may still be empty.
struct GitHubScanFilterDto: Codable, Equatable, Sendable {
    let labels: [String]
    let assignee: String?
    let active_label: String?
    let issue_types: [String]

    init(labels: [String], assignee: String? = nil, active_label: String? = nil, issue_types: [String] = []) {
        self.labels = labels
        self.assignee = assignee
        self.active_label = active_label
        self.issue_types = issue_types
    }
}

extension GitHubScanFilterDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        labels = try container.decodeIfPresent([String].self, forKey: .labels) ?? []
        assignee = try container.decodeIfPresent(String.self, forKey: .assignee)
        active_label = try container.decodeIfPresent(String.self, forKey: .active_label)
        issue_types = try container.decodeIfPresent([String].self, forKey: .issue_types) ?? []
    }
}

/// Auto Scan choices: `WorkspaceInfo.auto_scan` (absent on older agents) and a
/// template's `settings`. Each field is nil until chosen. `initial_prompt` is
/// set only in a template (a workspace keeps its prompt in `auto_scan_prompt`).
struct AutoScanSettingsDto: Codable, Equatable, Sendable {
    var source: String?
    var cli: String?
    var initial_prompt: String?
    var linear_filter: AutoScanFilterDto?
    var github_filter: GitHubScanFilterDto?
    var claude_options: ClaudeScanOptionsDto?
    var codex_options: CodexScanOptionsDto?
    var antigravity_options: AntigravityScanOptionsDto?
    var opencode_options: OpenCodeScanOptionsDto?
    var copilot_options: CopilotScanOptionsDto?
    var cursor_options: CursorScanOptionsDto?
    var junie_options: JunieScanOptionsDto?

    init(
        source: String? = nil,
        cli: String? = nil,
        initial_prompt: String? = nil,
        linear_filter: AutoScanFilterDto? = nil,
        github_filter: GitHubScanFilterDto? = nil,
        claude_options: ClaudeScanOptionsDto? = nil,
        codex_options: CodexScanOptionsDto? = nil,
        antigravity_options: AntigravityScanOptionsDto? = nil,
        opencode_options: OpenCodeScanOptionsDto? = nil,
        copilot_options: CopilotScanOptionsDto? = nil,
        cursor_options: CursorScanOptionsDto? = nil,
        junie_options: JunieScanOptionsDto? = nil
    ) {
        self.source = source
        self.cli = cli
        self.initial_prompt = initial_prompt
        self.linear_filter = linear_filter
        self.github_filter = github_filter
        self.claude_options = claude_options
        self.codex_options = codex_options
        self.antigravity_options = antigravity_options
        self.opencode_options = opencode_options
        self.copilot_options = copilot_options
        self.cursor_options = cursor_options
        self.junie_options = junie_options
    }
}

extension AutoScanSettingsDto {
    /// A bad value inside one agent's options drops only those options: a
    /// newer agent's shapes must never fail the whole workspace list.
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        source = try container.decodeIfPresent(String.self, forKey: .source)
        cli = try container.decodeIfPresent(String.self, forKey: .cli)
        initial_prompt = try container.decodeIfPresent(String.self, forKey: .initial_prompt)
        linear_filter = try container.decodeIfPresent(AutoScanFilterDto.self, forKey: .linear_filter)
        github_filter = try container.decodeIfPresent(GitHubScanFilterDto.self, forKey: .github_filter)
        claude_options = try? container.decodeIfPresent(ClaudeScanOptionsDto.self, forKey: .claude_options)
        codex_options = try? container.decodeIfPresent(CodexScanOptionsDto.self, forKey: .codex_options)
        antigravity_options = try? container.decodeIfPresent(AntigravityScanOptionsDto.self, forKey: .antigravity_options)
        opencode_options = try? container.decodeIfPresent(OpenCodeScanOptionsDto.self, forKey: .opencode_options)
        copilot_options = try? container.decodeIfPresent(CopilotScanOptionsDto.self, forKey: .copilot_options)
        cursor_options = try? container.decodeIfPresent(CursorScanOptionsDto.self, forKey: .cursor_options)
        junie_options = try? container.decodeIfPresent(JunieScanOptionsDto.self, forKey: .junie_options)
    }
}
