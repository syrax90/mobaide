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
        current_seq: Int64 = 0
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

struct StartScanRequest: Codable, Equatable, Sendable {
    let workspace_name: String
    let claude_options: ClaudeOptionsModel?

    private enum CodingKeys: String, CodingKey {
        case workspace_name = "project_name"
        case claude_options
    }

    init(workspace_name: String, claude_options: ClaudeOptionsModel? = nil) {
        self.workspace_name = workspace_name
        self.claude_options = claude_options
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
