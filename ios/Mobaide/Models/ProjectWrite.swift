import Foundation

/// Request body for `POST /api/projects`. Unlike `ProjectUpdate`,
/// `default_flags` is non-optional and always serialized — an empty map goes
/// out as `"default_flags": {}`, never dropped.
struct ProjectWrite: Codable, Equatable, Sendable {
    let name: String
    let path: String
    let linear_api_key: String?
    let conda_env: String?
    let default_flags: [String: JSONValue]
    let auto_scan_filter: AutoScanFilterDto?

    init(
        name: String,
        path: String,
        linear_api_key: String? = nil,
        conda_env: String? = nil,
        default_flags: [String: JSONValue] = [:],
        auto_scan_filter: AutoScanFilterDto? = nil
    ) {
        self.name = name
        self.path = path
        self.linear_api_key = linear_api_key
        self.conda_env = conda_env
        self.default_flags = default_flags
        self.auto_scan_filter = auto_scan_filter
    }
}

extension ProjectWrite {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        path = try container.decode(String.self, forKey: .path)
        linear_api_key = try container.decodeIfPresent(String.self, forKey: .linear_api_key)
        conda_env = try container.decodeIfPresent(String.self, forKey: .conda_env)
        default_flags = try container.decodeIfPresent([String: JSONValue].self, forKey: .default_flags) ?? [:]
        auto_scan_filter = try container.decodeIfPresent(AutoScanFilterDto.self, forKey: .auto_scan_filter)
    }
}
