import Foundation

/// The Herdr dashboard records (`GET /api/herdr`, the `herdr.changed` agent
/// event). Mobaide owns these shapes; both herdr products use them. `status`,
/// `agent`, `product` and `state` stay open strings so a value a newer agent
/// sends still decodes.
struct HerdrRect: Codable, Equatable, Sendable {
    let x: Int
    let y: Int
    let width: Int
    let height: Int
}

struct HerdrPane: Codable, Equatable, Sendable {
    let pane_id: String
    let title: String
    /// Nil for a pane without an agent, which then has no `status` either.
    let agent: String?
    let status: String?
    /// Changes each time the agent finishes a turn; see `HerdrSeenStore`.
    let turn: Int64?
    let cwd: String?
    let agent_session_id: String?
    let rect: HerdrRect?

    init(
        pane_id: String,
        title: String = "",
        agent: String? = nil,
        status: String? = nil,
        turn: Int64? = nil,
        cwd: String? = nil,
        agent_session_id: String? = nil,
        rect: HerdrRect? = nil
    ) {
        self.pane_id = pane_id
        self.title = title
        self.agent = agent
        self.status = status
        self.turn = turn
        self.cwd = cwd
        self.agent_session_id = agent_session_id
        self.rect = rect
    }
}

extension HerdrPane {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        pane_id = try container.decode(String.self, forKey: .pane_id)
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? ""
        agent = try container.decodeIfPresent(String.self, forKey: .agent)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        turn = try container.decodeIfPresent(Int64.self, forKey: .turn)
        cwd = try container.decodeIfPresent(String.self, forKey: .cwd)
        agent_session_id = try container.decodeIfPresent(String.self, forKey: .agent_session_id)
        rect = try container.decodeIfPresent(HerdrRect.self, forKey: .rect)
    }
}

struct HerdrTab: Codable, Equatable, Sendable {
    let tab_id: String
    let label: String
    let width: Int?
    let height: Int?
    let panes: [HerdrPane]

    init(tab_id: String, label: String = "", width: Int? = nil, height: Int? = nil, panes: [HerdrPane] = []) {
        self.tab_id = tab_id
        self.label = label
        self.width = width
        self.height = height
        self.panes = panes
    }
}

extension HerdrTab {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        tab_id = try container.decode(String.self, forKey: .tab_id)
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? ""
        width = try container.decodeIfPresent(Int.self, forKey: .width)
        height = try container.decodeIfPresent(Int.self, forKey: .height)
        panes = try container.decodeIfPresent([HerdrPane].self, forKey: .panes) ?? []
    }
}

struct HerdrWorkspace: Codable, Equatable, Sendable {
    let workspace_id: String
    let label: String
    let tabs: [HerdrTab]

    init(workspace_id: String, label: String = "", tabs: [HerdrTab] = []) {
        self.workspace_id = workspace_id
        self.label = label
        self.tabs = tabs
    }
}

extension HerdrWorkspace {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        workspace_id = try container.decode(String.self, forKey: .workspace_id)
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? ""
        tabs = try container.decodeIfPresent([HerdrTab].self, forKey: .tabs) ?? []
    }
}

struct HerdrAgentKind: Codable, Equatable, Sendable {
    let id: String
    let label: String
}

/// Every flag defaults to off: an action is offered only on an explicit `true`.
struct HerdrFeatures: Codable, Equatable, Sendable {
    let new_workspace: Bool
    let new_tab: Bool
    let split: Bool
    let rename: Bool
    let close_group: Bool
    let close_pane: Bool
    let control: Bool
    let type_in_watch: Bool
    let agents: [HerdrAgentKind]

    init(
        new_workspace: Bool = false,
        new_tab: Bool = false,
        split: Bool = false,
        rename: Bool = false,
        close_group: Bool = false,
        close_pane: Bool = false,
        control: Bool = false,
        type_in_watch: Bool = false,
        agents: [HerdrAgentKind] = []
    ) {
        self.new_workspace = new_workspace
        self.new_tab = new_tab
        self.split = split
        self.rename = rename
        self.close_group = close_group
        self.close_pane = close_pane
        self.control = control
        self.type_in_watch = type_in_watch
        self.agents = agents
    }
}

extension HerdrFeatures {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        func flag(_ key: CodingKeys) throws -> Bool {
            try container.decodeIfPresent(Bool.self, forKey: key) ?? false
        }
        new_workspace = try flag(.new_workspace)
        new_tab = try flag(.new_tab)
        split = try flag(.split)
        rename = try flag(.rename)
        close_group = try flag(.close_group)
        close_pane = try flag(.close_pane)
        control = try flag(.control)
        type_in_watch = try flag(.type_in_watch)
        agents = try container.decodeIfPresent([HerdrAgentKind].self, forKey: .agents) ?? []
    }
}

struct HerdrServer: Codable, Equatable, Sendable {
    static let productCore = "core"
    static let stateReady = "ready"

    let server_id: String
    /// `tui` (Herdr TUI) or `core` (herdr-core).
    let product: String
    let name: String
    let version: String?
    /// `ready` or `outdated`.
    let state: String
    let message: String?
    let features: HerdrFeatures
    let workspaces: [HerdrWorkspace]

    var isReady: Bool { state == Self.stateReady }

    init(
        server_id: String,
        product: String = "tui",
        name: String = "default",
        version: String? = nil,
        state: String = HerdrServer.stateReady,
        message: String? = nil,
        features: HerdrFeatures = HerdrFeatures(),
        workspaces: [HerdrWorkspace] = []
    ) {
        self.server_id = server_id
        self.product = product
        self.name = name
        self.version = version
        self.state = state
        self.message = message
        self.features = features
        self.workspaces = workspaces
    }
}

extension HerdrServer {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        server_id = try container.decode(String.self, forKey: .server_id)
        product = try container.decodeIfPresent(String.self, forKey: .product) ?? "tui"
        name = try container.decodeIfPresent(String.self, forKey: .name) ?? "default"
        version = try container.decodeIfPresent(String.self, forKey: .version)
        state = try container.decodeIfPresent(String.self, forKey: .state) ?? Self.stateReady
        message = try container.decodeIfPresent(String.self, forKey: .message)
        features = try container.decodeIfPresent(HerdrFeatures.self, forKey: .features) ?? HerdrFeatures()
        workspaces = try container.decodeIfPresent([HerdrWorkspace].self, forKey: .workspaces) ?? []
    }
}

struct HerdrSnapshot: Codable, Equatable, Sendable {
    let servers: [HerdrServer]

    init(servers: [HerdrServer] = []) {
        self.servers = servers
    }
}

extension HerdrSnapshot {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        servers = try container.decodeIfPresent([HerdrServer].self, forKey: .servers) ?? []
    }
}

/// `herdr.attention`: an agent in a pane needs the user.
struct HerdrAttention: Codable, Equatable, Sendable {
    let server_id: String
    let pane_id: String
    let reason: String
    let title: String
    let agent: String?
    let agent_id: String?

    init(
        server_id: String,
        pane_id: String,
        reason: String,
        title: String,
        agent: String? = nil,
        agent_id: String? = nil
    ) {
        self.server_id = server_id
        self.pane_id = pane_id
        self.reason = reason
        self.title = title
        self.agent = agent
        self.agent_id = agent_id
    }
}

struct HerdrPaneTarget: Codable, Equatable, Sendable {
    let server_id: String
    let pane_id: String
}

struct HerdrOpenPaneResponse: Codable, Equatable, Sendable {
    let session_id: String
}

struct HerdrControlRequest: Codable, Equatable, Sendable {
    let server_id: String
    let pane_id: String
    let control: Bool
    /// Names the client that takes or gives back control.
    let client_id: String
}

struct HerdrPaneTextResponse: Codable, Equatable, Sendable {
    let text: String
    let truncated: Bool

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? ""
        truncated = try container.decodeIfPresent(Bool.self, forKey: .truncated) ?? false
    }
}

struct HerdrCreateRequest: Codable, Equatable, Sendable {
    static let placementWorkspace = "workspace"
    static let placementTab = "tab"
    static let placementSplitRight = "split_right"
    static let placementSplitDown = "split_down"

    let server_id: String
    let placement: String
    var workspace_id: String? = nil
    var pane_id: String? = nil
    var cwd: String? = nil
    var label: String? = nil
    var agent: String? = nil
}

struct HerdrCreateResponse: Codable, Equatable, Sendable {
    let pane_id: String
    /// Set when the pane was made and its agent did not start: a message for the user.
    var agent_error: String? = nil
}

struct HerdrStartAgentRequest: Codable, Equatable, Sendable {
    let server_id: String
    let pane_id: String
    let agent: String
}

struct HerdrRenameRequest: Codable, Equatable, Sendable {
    let server_id: String
    let target: String
    let id: String
    let label: String
}

struct HerdrCloseRequest: Codable, Equatable, Sendable {
    let server_id: String
    let target: String
    let id: String
}

/// What a rename or a close acts on.
enum HerdrTarget: String, Sendable {
    case workspace
    case tab
    case pane
}
