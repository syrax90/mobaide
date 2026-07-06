import Foundation

/// `capabilities` is deliberately loose-typed heterogeneous JSON; the helper
/// walks below never throw — any missing key or shape mismatch returns nil so
/// old or malformed capability payloads degrade to "unknown" (treated as
/// optimistically available by callers).
struct VersionInfoResponse: Codable, Equatable, Sendable {
    let name: String
    let server_version: String
    let protocol_versions: [Int]
    let min_client_version: Int
    let capabilities: JSONValue
    let latest_agent_version: String?

    init(
        name: String,
        server_version: String,
        protocol_versions: [Int],
        min_client_version: Int,
        capabilities: JSONValue = .object([:]),
        latest_agent_version: String? = nil
    ) {
        self.name = name
        self.server_version = server_version
        self.protocol_versions = protocol_versions
        self.min_client_version = min_client_version
        self.capabilities = capabilities
        self.latest_agent_version = latest_agent_version
    }
}

extension VersionInfoResponse {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        server_version = try container.decode(String.self, forKey: .server_version)
        protocol_versions = try container.decode([Int].self, forKey: .protocol_versions)
        min_client_version = try container.decode(Int.self, forKey: .min_client_version)
        capabilities = try container.decodeIfPresent(JSONValue.self, forKey: .capabilities) ?? .object([:])
        latest_agent_version = try container.decodeIfPresent(String.self, forKey: .latest_agent_version)
    }

    func cliAvailable(_ cliId: String) -> Bool? {
        guard let value = capabilities["cli"]?[cliId]?["available"] else { return nil }
        switch value {
        case .bool(let flag): return flag
        case .string(let raw): return Bool(raw)
        default: return nil
        }
    }

    func cliModels(_ cliId: String) -> [String]? {
        guard let models = capabilities["cli"]?[cliId]?["models"]?.arrayValue else { return nil }
        return models.compactMap { element in
            switch element {
            case .string(let value): return value
            case .int(let value): return String(value)
            case .double(let value): return String(value)
            case .bool(let value): return String(value)
            default: return nil
            }
        }
    }
}
