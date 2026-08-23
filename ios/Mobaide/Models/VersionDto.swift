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

    /// Per-model reasoning-effort sets from `capabilities.cli.<id>.model_details`
    /// (today only Codex populates it, from `codex debug models`). Entries with
    /// a blank id or no usable efforts are dropped; nil when nothing usable
    /// remains — an older agent or a failed probe — so callers fall back to
    /// their static effort list. Never throws, like the walks above.
    func cliModelDetails(_ cliId: String) -> [CliModelDetail]? {
        guard let entries = capabilities["cli"]?[cliId]?["model_details"]?.arrayValue else {
            return nil
        }
        let parsed = entries.compactMap { entry -> CliModelDetail? in
            guard case .string(let id)? = entry["id"], !id.isEmpty else { return nil }
            guard let effortValues = entry["efforts"]?.arrayValue else { return nil }
            let efforts = effortValues.compactMap { value -> String? in
                guard case .string(let effort) = value, !effort.isEmpty else { return nil }
                return effort
            }
            guard !efforts.isEmpty else { return nil }
            var defaultEffort: String?
            if case .string(let value)? = entry["default_effort"], !value.isEmpty {
                defaultEffort = value
            }
            return CliModelDetail(id: id, efforts: efforts, defaultEffort: defaultEffort)
        }
        return parsed.isEmpty ? nil : parsed
    }
}

/// One entry of `capabilities.cli.<id>.model_details`: the reasoning efforts a
/// model supports and (when the catalog names one) its default effort. Built
/// only by the loose walk in `cliModelDetails` — never decoded directly.
struct CliModelDetail: Equatable, Sendable {
    let id: String
    let efforts: [String]
    let defaultEffort: String?
}
