import Foundation

/// App-scoped live session list (Android `SessionRepository`). The list is
/// push-driven off the agent event bus — `sessions.changed` replaces the whole
/// snapshot, `session.updated`/`session.attention` upsert by id — so no screen
/// polls REST for it. Create/delete are thin pass-throughs to `AgentAPI`.
@MainActor
final class SessionRepository {
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let terminalSessionStore: TerminalSessionStore
    private let broadcaster = Broadcaster<[SessionResponse]>.stateFlow()

    private(set) var currentSessions: [SessionResponse] = []
    private var subscription: Task<Void, Never>?

    init(agentAPI: AgentAPI, agentEventBus: AgentEventBus, terminalSessionStore: TerminalSessionStore) {
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.terminalSessionStore = terminalSessionStore
        broadcaster.emit([])
    }

    func sessions() -> AsyncStream<[SessionResponse]> {
        broadcaster.subscribe()
    }

    /// Idempotent. Subscribe before `AgentEventBus.start()` so the snapshot the
    /// bus re-pushes on connect is not missed (the events stream has no replay).
    func start() {
        guard subscription == nil else { return }
        let stream = agentEventBus.events()
        subscription = Task { [weak self] in
            for await event in stream {
                await self?.apply(event)
            }
        }
    }

    private func apply(_ event: AgentEvent) {
        switch event {
        case .sessionsChanged(let list):
            setSessions(list)
        case .sessionUpdated(let session), .sessionAttention(let session):
            upsert(session)
        default:
            break
        }
    }

    /// Removal-driven eviction relies on `sessions.changed` always carrying the
    /// complete authoritative list (the agent re-pushes a full snapshot on every
    /// connect, and `upsert` only ever adds/replaces). A partial snapshot here
    /// would wrongly evict a still-live terminal.
    private func setSessions(_ list: [SessionResponse]) {
        let survivingIds = Set(list.map(\.session_id))
        let removedIds = currentSessions.map(\.session_id).filter { !survivingIds.contains($0) }
        currentSessions = list
        for id in removedIds {
            terminalSessionStore.evict(id)
        }
        broadcaster.emit(list)
    }

    private func upsert(_ session: SessionResponse) {
        var list = currentSessions
        if let index = list.firstIndex(where: { $0.session_id == session.session_id }) {
            list[index] = session
        } else {
            list.append(session)
        }
        setSessions(list)
    }

    func createSession(_ request: CreateSessionRequest) async throws -> SessionResponse {
        let response = try await agentAPI.createSession(request)
        // Publish the created session before the caller navigates into its
        // terminal: the terminal screen resolves the session kind (per-kind
        // control keys) from `currentSessions`, and the bus `sessions.changed`
        // snapshot arrives a network round-trip later. Reconciled idempotently
        // by that later snapshot.
        upsert(response)
        return response
    }

    func deleteSession(id: String, force: Bool) async throws {
        try await agentAPI.deleteSession(sessionId: id, force: force)
        terminalSessionStore.evict(id)
    }

    @discardableResult
    func listSessions() async throws -> [SessionResponse] {
        let list = try await agentAPI.listSessions()
        setSessions(list)
        return list
    }
}
