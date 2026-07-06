import Foundation

/// `default_flags` values are heterogeneous on the wire (bool | int | float |
/// str), kept as raw `JSONValue` for the UI layer to coerce by flag type.
struct ProjectInfo: Codable, Equatable, Sendable {
    let name: String
    let path: String
    let has_linear_key: Bool
    let is_git: Bool
    let default_flags: [String: JSONValue]
    let conda_env: String?
    let order: Int
    let path_exists: Bool
    let auto_scan_filter: AutoScanFilterDto
    let auto_scan_prompt: String?

    init(
        name: String,
        path: String,
        has_linear_key: Bool = false,
        is_git: Bool = false,
        default_flags: [String: JSONValue] = [:],
        conda_env: String? = nil,
        order: Int = 0,
        path_exists: Bool = true,
        auto_scan_filter: AutoScanFilterDto = AutoScanFilterDto(),
        auto_scan_prompt: String? = nil
    ) {
        self.name = name
        self.path = path
        self.has_linear_key = has_linear_key
        self.is_git = is_git
        self.default_flags = default_flags
        self.conda_env = conda_env
        self.order = order
        self.path_exists = path_exists
        self.auto_scan_filter = auto_scan_filter
        self.auto_scan_prompt = auto_scan_prompt
    }
}

extension ProjectInfo {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        path = try container.decode(String.self, forKey: .path)
        has_linear_key = try container.decodeIfPresent(Bool.self, forKey: .has_linear_key) ?? false
        is_git = try container.decodeIfPresent(Bool.self, forKey: .is_git) ?? false
        default_flags = try container.decodeIfPresent([String: JSONValue].self, forKey: .default_flags) ?? [:]
        conda_env = try container.decodeIfPresent(String.self, forKey: .conda_env)
        order = try container.decodeIfPresent(Int.self, forKey: .order) ?? 0
        path_exists = try container.decodeIfPresent(Bool.self, forKey: .path_exists) ?? true
        auto_scan_filter = try container.decodeIfPresent(AutoScanFilterDto.self, forKey: .auto_scan_filter) ?? AutoScanFilterDto()
        auto_scan_prompt = try container.decodeIfPresent(String.self, forKey: .auto_scan_prompt)
    }
}
