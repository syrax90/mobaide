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

    /// Whether the agent's startup model enumeration was still running when
    /// this response was built (`capabilities.cli.<id>.models_pending`): the
    /// absence of `models`/`model_details` is then provisional, not
    /// authoritative — callers keep persisted catalog selections instead of
    /// reconciling them away, and may re-probe. Absent key or any unreadable
    /// shape reads as `false`.
    func cliModelsPending(_ cliId: String) -> Bool {
        guard case .bool(let flag)? = capabilities["cli"]?[cliId]?["models_pending"] else {
            return false
        }
        return flag
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

/// The agent's `capabilities.git` mutation keys. Every mutating control hangs
/// off an explicit `true`, so an older agent keeps a read-only Changes viewer.
struct GitCapabilities: Equatable, Sendable {
    var stage = false
    var discard = false
    var commit = false
    var fetch = false
    var pull = false
    var push = false
    /// The project Git screen's remote rows, New branch and Check out.
    var branches = false
    /// *Rebase…* and the Changes screen's Continue / Skip / Abort.
    var rebase = false
    /// `stage` refuses a conflicted file that still holds markers, so *Mark resolved* is safe.
    var resolve = false
    /// The project Git screen may delete remote-tracking rows (on the remote).
    var deleteRemote = false
    /// The conflict resolver's routes and `resolved_paths` on status.
    var conflicts = false

    /// The *Fetch / Pull / Push…* entry and the Sync button exist on any of the three.
    var hasSync: Bool { fetch || pull || push }

    static let none = GitCapabilities()
}

extension VersionInfoResponse {
    /// Whether the agent advertises `capabilities.git.<key>` as `true`. The
    /// opposite default from `cliAvailable`: absent or unreadable reads as
    /// `false`, so no Stage is offered that would 404.
    func gitCapability(_ key: String) -> Bool {
        if case .bool(true)? = capabilities["git"]?[key] { return true }
        return false
    }

    /// `capabilities.fs.<key>`, with `gitCapability`'s explicit-`true` rule.
    func fsCapability(_ key: String) -> Bool {
        if case .bool(true)? = capabilities["fs"]?[key] { return true }
        return false
    }

    var gitCapabilities: GitCapabilities {
        GitCapabilities(
            stage: gitCapability("stage"),
            discard: gitCapability("discard"),
            commit: gitCapability("commit"),
            fetch: gitCapability("fetch"),
            pull: gitCapability("pull"),
            push: gitCapability("push"),
            branches: gitCapability("branches"),
            rebase: gitCapability("rebase"),
            resolve: gitCapability("resolve"),
            deleteRemote: gitCapability("delete_remote"),
            conflicts: gitCapability("conflicts")
        )
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
