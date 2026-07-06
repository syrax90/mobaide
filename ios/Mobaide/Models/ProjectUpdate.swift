import Foundation

/// Three-state PATCH body for `PUT /api/projects/{name}`: a field omitted from
/// the JSON keeps the existing value, `""` clears it, a non-empty value
/// replaces it. For `default_flags`: omitted = keep, `{}` = clear, non-empty =
/// replace. `auto_scan_filter` has no clear state (omitted = keep only).
/// Encoding relies on synthesized `encodeIfPresent`: nil fields vanish from
/// the wire (kotlinx `explicitNulls = false; encodeDefaults = false` parity).
struct ProjectUpdate: Codable, Equatable, Sendable {
    let name: String?
    let path: String?
    let linear_api_key: String?
    let conda_env: String?
    let default_flags: [String: JSONValue]?
    let auto_scan_filter: AutoScanFilterDto?

    init(
        name: String? = nil,
        path: String? = nil,
        linear_api_key: String? = nil,
        conda_env: String? = nil,
        default_flags: [String: JSONValue]? = nil,
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
