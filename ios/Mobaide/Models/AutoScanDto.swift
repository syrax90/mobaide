import Foundation

struct AutoScanInstanceDto: Codable, Equatable, Sendable {
    let project_name: String
    let status: String
    let started_at: Double
    let last_activity_at: Double
    let processed_count: Int
    let last_scan_time: Double?
    let scan_interval_seconds: Int
    let next_scan_at: String?
    let cycle_number: Int
    let current_seq: Int64

    init(
        project_name: String,
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
        self.project_name = project_name
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
        project_name = try container.decode(String.self, forKey: .project_name)
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
    let available_projects: [String]
    let auto_scan_prompt: String?

    init(
        scans: [AutoScanInstanceDto] = [],
        capacity: Int = 0,
        max_scans: Int = 5,
        available_projects: [String] = [],
        auto_scan_prompt: String? = nil
    ) {
        self.scans = scans
        self.capacity = capacity
        self.max_scans = max_scans
        self.available_projects = available_projects
        self.auto_scan_prompt = auto_scan_prompt
    }
}

extension AutoScanListResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        scans = try container.decodeIfPresent([AutoScanInstanceDto].self, forKey: .scans) ?? []
        capacity = try container.decodeIfPresent(Int.self, forKey: .capacity) ?? 0
        max_scans = try container.decodeIfPresent(Int.self, forKey: .max_scans) ?? 5
        available_projects = try container.decodeIfPresent([String].self, forKey: .available_projects) ?? []
        auto_scan_prompt = try container.decodeIfPresent(String.self, forKey: .auto_scan_prompt)
    }
}

struct StartScanRequest: Codable, Equatable, Sendable {
    let project_name: String
    let claude_options: ClaudeOptionsModel?

    init(project_name: String, claude_options: ClaudeOptionsModel? = nil) {
        self.project_name = project_name
        self.claude_options = claude_options
    }
}

struct StopScanRequest: Codable, Equatable, Sendable {
    let project_name: String
}

struct RunNowRequest: Codable, Equatable, Sendable {
    let project_name: String
}

struct RunNowResponse: Codable, Equatable, Sendable {
    let ok: Bool
    let project_name: String
    let next_scan_at: String?
}
