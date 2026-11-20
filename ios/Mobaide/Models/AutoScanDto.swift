import Foundation

struct AutoScanInstanceDto: Codable, Equatable, Sendable {
    let workspace_name: String
    let status: String
    let started_at: Double
    let last_activity_at: Double
    let processed_count: Int
    let last_scan_time: Double?
    let scan_interval_seconds: Int
    let next_scan_at: String?
    let cycle_number: Int
    let current_seq: Int64
    /// `linear` or `github`; an older agent sends neither field.
    let source: String
    /// The agent kind each issue runs in (`claude`, `codex`, …).
    let cli: String
    /// How each issue's session opens: `terminal` or `chat`.
    let ui: String

    private enum CodingKeys: String, CodingKey {
        case workspace_name = "project_name"
        case status
        case started_at
        case last_activity_at
        case processed_count
        case last_scan_time
        case scan_interval_seconds
        case next_scan_at
        case cycle_number
        case current_seq
        case source
        case cli
        case ui
    }

    init(
        workspace_name: String,
        status: String,
        started_at: Double,
        last_activity_at: Double,
        processed_count: Int = 0,
        last_scan_time: Double? = nil,
        scan_interval_seconds: Int = 300,
        next_scan_at: String? = nil,
        cycle_number: Int = 0,
        current_seq: Int64 = 0,
        source: String = "linear",
        cli: String = "claude",
        ui: String = "terminal"
    ) {
        self.workspace_name = workspace_name
        self.status = status
        self.started_at = started_at
        self.last_activity_at = last_activity_at
        self.processed_count = processed_count
        self.last_scan_time = last_scan_time
        self.scan_interval_seconds = scan_interval_seconds
        self.next_scan_at = next_scan_at
        self.cycle_number = cycle_number
        self.current_seq = current_seq
        self.source = source
        self.cli = cli
        self.ui = ui
    }
}

extension AutoScanInstanceDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        workspace_name = try container.decode(String.self, forKey: .workspace_name)
        status = try container.decode(String.self, forKey: .status)
        started_at = try container.decode(Double.self, forKey: .started_at)
        last_activity_at = try container.decode(Double.self, forKey: .last_activity_at)
        processed_count = try container.decodeIfPresent(Int.self, forKey: .processed_count) ?? 0
        last_scan_time = try container.decodeIfPresent(Double.self, forKey: .last_scan_time)
        scan_interval_seconds = try container.decodeIfPresent(Int.self, forKey: .scan_interval_seconds) ?? 300
        next_scan_at = try container.decodeIfPresent(String.self, forKey: .next_scan_at)
        cycle_number = try container.decodeIfPresent(Int.self, forKey: .cycle_number) ?? 0
        current_seq = try container.decodeIfPresent(Int64.self, forKey: .current_seq) ?? 0
        source = try container.decodeIfPresent(String.self, forKey: .source) ?? "linear"
        cli = try container.decodeIfPresent(String.self, forKey: .cli) ?? "claude"
        ui = try container.decodeIfPresent(String.self, forKey: .ui) ?? "terminal"
    }
}

struct AutoScanListResponse: Codable, Equatable, Sendable {
    let scans: [AutoScanInstanceDto]
    let capacity: Int
    let max_scans: Int
    let available_workspaces: [String]
    let auto_scan_prompt: String?

    private enum CodingKeys: String, CodingKey {
        case scans
        case capacity
        case max_scans
        case available_workspaces = "available_projects"
        case auto_scan_prompt
    }

    init(
        scans: [AutoScanInstanceDto] = [],
        capacity: Int = 0,
        max_scans: Int = 5,
        available_workspaces: [String] = [],
        auto_scan_prompt: String? = nil
    ) {
        self.scans = scans
        self.capacity = capacity
        self.max_scans = max_scans
        self.available_workspaces = available_workspaces
        self.auto_scan_prompt = auto_scan_prompt
    }
}

extension AutoScanListResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        scans = try container.decodeIfPresent([AutoScanInstanceDto].self, forKey: .scans) ?? []
        capacity = try container.decodeIfPresent(Int.self, forKey: .capacity) ?? 0
        max_scans = try container.decodeIfPresent(Int.self, forKey: .max_scans) ?? 5
        available_workspaces = try container.decodeIfPresent([String].self, forKey: .available_workspaces) ?? []
        auto_scan_prompt = try container.decodeIfPresent(String.self, forKey: .auto_scan_prompt)
    }
}

/// `POST /api/autoscan/start`. An older client sends only `project_name` and
/// `claude_options`; a client of an agent with `capabilities.autoscan` sends
/// every choice: `source`, `cli`, that source's filter, the chosen agent's
/// options (Pi has none), `ui` (`terminal` / `chat`), `interval_minutes`
/// (1–1440) and `initial_prompt` (`""` clears the saved prompt).
/// Nil fields stay off the wire.
struct StartScanRequest: Codable, Equatable, Sendable {
    let workspace_name: String
    let claude_options: ClaudeOptionsModel?
    let codex_options: CodexScanOptionsDto?
    let antigravity_options: AntigravityScanOptionsDto?
    let opencode_options: OpenCodeScanOptionsDto?
    let copilot_options: CopilotScanOptionsDto?
    let cursor_options: CursorScanOptionsDto?
    let junie_options: JunieScanOptionsDto?
    let ui: String?
    let interval_minutes: Int?
    let source: String?
    let cli: String?
    let initial_prompt: String?
    let linear_filter: AutoScanFilterDto?
    let github_filter: GitHubScanFilterDto?
    let linear_api_key: String?

    private enum CodingKeys: String, CodingKey {
        case workspace_name = "project_name"
        case claude_options
        case codex_options
        case antigravity_options
        case opencode_options
        case copilot_options
        case cursor_options
        case junie_options
        case ui
        case interval_minutes
        case source
        case cli
        case initial_prompt
        case linear_filter
        case github_filter
        case linear_api_key
    }

    init(
        workspace_name: String,
        claude_options: ClaudeOptionsModel? = nil,
        codex_options: CodexScanOptionsDto? = nil,
        antigravity_options: AntigravityScanOptionsDto? = nil,
        opencode_options: OpenCodeScanOptionsDto? = nil,
        copilot_options: CopilotScanOptionsDto? = nil,
        cursor_options: CursorScanOptionsDto? = nil,
        junie_options: JunieScanOptionsDto? = nil,
        ui: String? = nil,
        interval_minutes: Int? = nil,
        source: String? = nil,
        cli: String? = nil,
        initial_prompt: String? = nil,
        linear_filter: AutoScanFilterDto? = nil,
        github_filter: GitHubScanFilterDto? = nil,
        linear_api_key: String? = nil
    ) {
        self.workspace_name = workspace_name
        self.claude_options = claude_options
        self.codex_options = codex_options
        self.antigravity_options = antigravity_options
        self.opencode_options = opencode_options
        self.copilot_options = copilot_options
        self.cursor_options = cursor_options
        self.junie_options = junie_options
        self.ui = ui
        self.interval_minutes = interval_minutes
        self.source = source
        self.cli = cli
        self.initial_prompt = initial_prompt
        self.linear_filter = linear_filter
        self.github_filter = github_filter
        self.linear_api_key = linear_api_key
    }
}

struct StopScanRequest: Codable, Equatable, Sendable {
    let workspace_name: String

    private enum CodingKeys: String, CodingKey {
        case workspace_name = "project_name"
    }
}

struct RunNowRequest: Codable, Equatable, Sendable {
    let workspace_name: String

    private enum CodingKeys: String, CodingKey {
        case workspace_name = "project_name"
    }
}

struct RunNowResponse: Codable, Equatable, Sendable {
    let ok: Bool
    let workspace_name: String
    let next_scan_at: String?

    private enum CodingKeys: String, CodingKey {
        case ok
        case workspace_name = "project_name"
        case next_scan_at
    }
}

/// `GET /api/autoscan/github/status`: what a GitHub scan of the workspace still
/// needs. `problem` is the first blocking reason in plain words, nil when a
/// scan can start; `can_install` says whether the setup route knows an
/// installer for the PC, and `install_command` is that command as text.
struct GitHubStatusResponse: Codable, Equatable, Sendable {
    let installed: Bool
    let version: String?
    let signed_in: Bool
    let account: String?
    let host: String?
    let repo: String?
    let repo_url: String?
    let problem: String?
    /// Nil from an agent that predates it: then the host's name decides.
    let github_host: Bool?
    let can_install: Bool
    let install_command: String?

    /// Signing in can help: the remote is on GitHub (github.com or an Enterprise Server).
    var offersSignIn: Bool {
        !signed_in && (github_host ?? (host?.contains("github") == true))
    }

    init(
        installed: Bool = false,
        version: String? = nil,
        signed_in: Bool = false,
        account: String? = nil,
        host: String? = nil,
        repo: String? = nil,
        repo_url: String? = nil,
        problem: String? = nil,
        github_host: Bool? = nil,
        can_install: Bool = false,
        install_command: String? = nil
    ) {
        self.installed = installed
        self.version = version
        self.signed_in = signed_in
        self.account = account
        self.host = host
        self.repo = repo
        self.repo_url = repo_url
        self.problem = problem
        self.github_host = github_host
        self.can_install = can_install
        self.install_command = install_command
    }
}

extension GitHubStatusResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        installed = try container.decodeIfPresent(Bool.self, forKey: .installed) ?? false
        version = try container.decodeIfPresent(String.self, forKey: .version)
        signed_in = try container.decodeIfPresent(Bool.self, forKey: .signed_in) ?? false
        account = try container.decodeIfPresent(String.self, forKey: .account)
        host = try container.decodeIfPresent(String.self, forKey: .host)
        repo = try container.decodeIfPresent(String.self, forKey: .repo)
        repo_url = try container.decodeIfPresent(String.self, forKey: .repo_url)
        problem = try container.decodeIfPresent(String.self, forKey: .problem)
        github_host = try container.decodeIfPresent(Bool.self, forKey: .github_host)
        can_install = try container.decodeIfPresent(Bool.self, forKey: .can_install) ?? false
        install_command = try container.decodeIfPresent(String.self, forKey: .install_command)
    }
}

struct GitHubLabelDto: Codable, Equatable, Sendable {
    let name: String
    let color: String

    init(name: String, color: String = "") {
        self.name = name
        self.color = color
    }
}

extension GitHubLabelDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        color = try container.decodeIfPresent(String.self, forKey: .color) ?? ""
    }
}

/// A GitHub issue type of the repository (Bug, Task, Feature…). `color` is
/// GitHub's color name (`RED`, `BLUE`…).
struct GitHubIssueTypeDto: Codable, Equatable, Sendable {
    let name: String
    let color: String
    let description: String

    init(name: String, color: String = "", description: String = "") {
        self.name = name
        self.color = color
        self.description = description
    }
}

extension GitHubIssueTypeDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        color = try container.decodeIfPresent(String.self, forKey: .color) ?? ""
        description = try container.decodeIfPresent(String.self, forKey: .description) ?? ""
    }
}

/// `GET /api/autoscan/github/labels`: the repository's labels and its issue
/// types (empty when it has none or GitHub did not say).
struct GitHubLabelsResponse: Codable, Equatable, Sendable {
    let labels: [GitHubLabelDto]
    let issue_types: [GitHubIssueTypeDto]

    init(labels: [GitHubLabelDto], issue_types: [GitHubIssueTypeDto] = []) {
        self.labels = labels
        self.issue_types = issue_types
    }
}

extension GitHubLabelsResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        labels = try container.decodeIfPresent([GitHubLabelDto].self, forKey: .labels) ?? []
        issue_types = try container.decodeIfPresent([GitHubIssueTypeDto].self, forKey: .issue_types) ?? []
    }
}

/// A named set of Auto Scan choices saved on the PC (`GET /api/autoscan/templates`).
struct AutoScanTemplateDto: Codable, Equatable, Sendable, Identifiable {
    let id: String
    let name: String
    let updated_at: String
    let settings: AutoScanSettingsDto
}

extension AutoScanTemplateDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        updated_at = try container.decodeIfPresent(String.self, forKey: .updated_at) ?? ""
        settings = try container.decodeIfPresent(AutoScanSettingsDto.self, forKey: .settings) ?? AutoScanSettingsDto()
    }
}

/// Every template of the PC, sorted by name: the answer of the list, save and
/// delete routes.
struct AutoScanTemplatesResponse: Codable, Equatable, Sendable {
    let templates: [AutoScanTemplateDto]

    init(templates: [AutoScanTemplateDto] = []) {
        self.templates = templates
    }
}

extension AutoScanTemplatesResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        templates = try container.decodeIfPresent([AutoScanTemplateDto].self, forKey: .templates) ?? []
    }
}

/// `POST /api/autoscan/templates`: a template with the same name (ignoring
/// case) is replaced.
struct SaveAutoScanTemplateRequest: Codable, Equatable, Sendable {
    let name: String
    let settings: AutoScanSettingsDto
}

enum GitHubSetupAction: String, Codable, Sendable {
    case install
    case signIn = "sign_in"
}

/// `POST /api/autoscan/github/setup`: opens a terminal session on the PC that
/// installs the GitHub CLI or signs it in. Answers with a session dict.
struct GitHubSetupRequest: Codable, Equatable, Sendable {
    let workspace_name: String
    let action: GitHubSetupAction
    let rows: Int
    let cols: Int

    private enum CodingKeys: String, CodingKey {
        case workspace_name = "project_name"
        case action
        case rows
        case cols
    }

    init(workspace_name: String, action: GitHubSetupAction, rows: Int = 24, cols: Int = 80) {
        self.workspace_name = workspace_name
        self.action = action
        self.rows = rows
        self.cols = cols
    }
}
