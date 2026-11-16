import Foundation

/// A Herdr refusal: the agent's own words and its error code.
struct HerdrError: Error, Equatable, Sendable {
    let code: String?
    let message: String

    /// The pane is closed or its herdr server stopped: retrying cannot help.
    var isGone: Bool { code == "pane_not_found" || code == "server_not_found" }

    static let fallbackMessage = "Herdr did not answer. Try again."

    /// `{"detail": {"code", "message"}}`, or a plain string for a relay error.
    init(_ error: any Error) {
        guard let api = error as? APIError,
              let data = api.errorBody.data(using: .utf8),
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            self.init(code: nil, message: Self.fallbackMessage)
            return
        }
        if let detail = body["detail"] as? [String: Any] {
            let message = (detail["message"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            self.init(code: detail["code"] as? String, message: message ?? Self.fallbackMessage)
        } else if let detail = body["detail"] as? String, !detail.isEmpty {
            self.init(code: nil, message: detail)
        } else {
            self.init(code: nil, message: Self.fallbackMessage)
        }
    }

    init(code: String?, message: String) {
        self.code = code
        self.message = message
    }
}

/// The Herdr servers on the connected agent's computer (Android
/// `HerdrRepository`). `herdr.changed` carries the whole state on every change
/// and on every events-socket connect, so the UI needs no refetch after an
/// action. The snapshot is nil until the first state arrives and for an agent
/// without the dashboard.
@MainActor
final class HerdrRepository {
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let connectedAgentId: () -> String?
    private let broadcaster = Broadcaster<HerdrSnapshot?>.stateFlow()

    private(set) var currentSnapshot: HerdrSnapshot?
    /// The agent the current snapshot belongs to.
    private var loadedAgentId: String?
    private var subscription: Task<Void, Never>?
    private var recheckTask: Task<Void, Never>?
    private var recheckPending = false
    /// Counts the pushed states, so a REST read can tell it is older.
    private var pushes = 0

    init(agentAPI: AgentAPI, agentEventBus: AgentEventBus, connectedAgentId: @escaping () -> String?) {
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.connectedAgentId = connectedAgentId
        broadcaster.emit(nil)
    }

    /// The id of the connected agent; part of every per-device pane key.
    var agentId: String { connectedAgentId() ?? "" }

    func snapshots() -> AsyncStream<HerdrSnapshot?> { broadcaster.subscribe() }

    /// Idempotent. Subscribe before `AgentEventBus.start()` so the snapshot the
    /// bus re-pushes on connect is not missed (the events stream has no replay).
    func start() {
        guard subscription == nil else { return }
        let stream = agentEventBus.events()
        subscription = Task { [weak self] in
            for await event in stream {
                switch event {
                case .herdrChanged(let snapshot):
                    await self?.setPushed(snapshot)
                case .sessionsChanged:
                    // `herdr.changed` arrives only while the agent has the
                    // dashboard, so a held state is re-checked on each sessions
                    // list (sent on every connect).
                    await self?.scheduleRecheck()
                default:
                    break
                }
            }
        }
    }

    /// Check the held state in a task of its own, one at a time. The check is
    /// a REST read: run in the event loop it would hold back `herdr.changed`,
    /// and a burst of events would run one read after another.
    private func scheduleRecheck() {
        guard currentSnapshot != nil else { return }
        if recheckTask != nil {
            recheckPending = true
            return
        }
        recheckTask = Task { [weak self] in
            repeat {
                self?.recheckPending = false
                await self?.recheckCapability()
            } while self?.recheckPending == true
            self?.recheckTask = nil
        }
    }

    /// Drop the held state when the agent no longer has the dashboard. Reads
    /// the version only: the state itself arrives as `herdr.changed`, and a
    /// REST copy read here could replace a newer pushed one.
    private func recheckCapability() async {
        guard let version = try? await agentAPI.getAgentVersion() else { return }
        if !version.herdrCapability, currentSnapshot != nil { set(nil) }
    }

    private func setPushed(_ snapshot: HerdrSnapshot) {
        pushes &+= 1
        set(snapshot)
    }

    private func set(_ snapshot: HerdrSnapshot?) {
        loadedAgentId = connectedAgentId()
        currentSnapshot = snapshot
        broadcaster.emit(snapshot)
    }

    /// Seed the snapshot over REST. Another agent's snapshot is dropped first.
    /// An agent without `capabilities.herdr` clears it. A failed read keeps
    /// what this agent sent last: the events stream corrects it.
    func refresh() async {
        if connectedAgentId() != loadedAgentId {
            // Another agent was selected: its predecessor's servers are not its own.
            set(nil)
        }
        // A failed read says nothing: keep what this agent sent last.
        guard let version = try? await agentAPI.getAgentVersion() else { return }
        guard version.herdrCapability else {
            // The agent has no dashboard (any more): its servers must not stay on screen.
            set(nil)
            return
        }
        let pushesBefore = pushes
        // A `herdr.changed` that came during the read is newer than its answer.
        if let snapshot = try? await agentAPI.getHerdr(), pushes == pushesBefore {
            set(snapshot)
        }
    }

    func openPane(serverId: String, paneId: String) async throws -> String {
        try await call { try await self.agentAPI.openHerdrPane(serverId: serverId, paneId: paneId).session_id }
    }

    /// Take control of a pane as this app, or give it back. Only the
    /// controller can give it back.
    func setControl(serverId: String, paneId: String, control: Bool) async throws {
        try await call {
            try await self.agentAPI.setHerdrControl(
                serverId: serverId, paneId: paneId, control: control, clientId: PaneClient.id
            )
        }
    }

    func paneText(serverId: String, paneId: String, lines: Int) async throws -> HerdrPaneTextResponse {
        try await call { try await self.agentAPI.getHerdrPaneText(serverId: serverId, paneId: paneId, lines: lines) }
    }

    /// Create a workspace, a tab or a split. The answer names the new pane,
    /// and carries a message when the pane was made and its agent did not start.
    func create(_ request: HerdrCreateRequest) async throws -> HerdrCreateResponse {
        try await call { try await self.agentAPI.createHerdrTerminal(request) }
    }

    func startAgent(serverId: String, paneId: String, agent: String) async throws {
        try await call {
            try await self.agentAPI.startHerdrAgent(serverId: serverId, paneId: paneId, agent: agent)
        }
    }

    func rename(serverId: String, target: HerdrTarget, id: String, label: String) async throws {
        try await call {
            try await self.agentAPI.renameHerdrItem(
                HerdrRenameRequest(server_id: serverId, target: target.rawValue, id: id, label: label)
            )
        }
    }

    func close(serverId: String, target: HerdrTarget, id: String) async throws {
        try await call {
            try await self.agentAPI.closeHerdrItem(
                HerdrCloseRequest(server_id: serverId, target: target.rawValue, id: id)
            )
        }
    }

    private func call<T: Sendable>(_ block: @escaping @Sendable () async throws -> T) async throws -> T {
        do {
            return try await block()
        } catch {
            throw HerdrError(error)
        }
    }
}
