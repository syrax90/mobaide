import Foundation
import Observation
import SwiftUI

/// Sessions list view state (Android `SessionListViewModel`). The list is
/// bus-driven via `SessionRepository`; connection state drives the reconnect
/// spinner. Auto-resume opens the last running session once on first load.
@MainActor
@Observable
final class SessionListModel {
    private(set) var sessions: [SessionResponse] = []
    private(set) var activeScans: [AutoScanInstanceDto] = []
    private(set) var isReconnecting = false
    private(set) var error: String?
    private(set) var notice: String?
    /// Live custom-type names keyed on the full wire kind (`custom_<id>`),
    /// the second link of the label chain (`kind_label` snapshot → this →
    /// "Custom").
    private(set) var customTypeNames: [String: String] = [:]

    private let repository: SessionRepository
    private let agentEventBus: AgentEventBus
    private let preferencesStore: PreferencesStore
    private let autoScanRepository: AutoScanRepository
    private let customAgentTypesRepository: CustomAgentTypesRepository
    // `.latestClearable` so the one-shot auto-resume id survives the race where
    // `observeSessions` emits before the screen's `autoResumeEvents` task
    // subscribes, while `clearAutoResume()` (called by the screen after it
    // navigates) drops the cache so returning to the list does not replay it and
    // re-navigate. It fires at most once per model instance (didAttemptAutoResume).
    private let navBroadcaster = Broadcaster<String>(replay: .latestClearable)
    private var didAttemptAutoResume = false

    init(
        repository: SessionRepository,
        agentEventBus: AgentEventBus,
        preferencesStore: PreferencesStore,
        autoScanRepository: AutoScanRepository,
        customAgentTypesRepository: CustomAgentTypesRepository
    ) {
        self.repository = repository
        self.agentEventBus = agentEventBus
        self.preferencesStore = preferencesStore
        self.autoScanRepository = autoScanRepository
        self.customAgentTypesRepository = customAgentTypesRepository
        self.sessions = repository.currentSessions
        self.activeScans = autoScanRepository.currentScans
        self.customTypeNames = Self.typeNames(customAgentTypesRepository.currentTypes)
    }

    var isEmpty: Bool { sessions.isEmpty && !isReconnecting }

    func autoResumeEvents() -> AsyncStream<String> { navBroadcaster.subscribe() }

    /// Called by the screen once it has consumed the auto-resume id and
    /// navigated, so returning to the list does not replay the cached id.
    func clearAutoResume() { navBroadcaster.clearReplay() }

    func observeSessions() async {
        for await list in repository.sessions() {
            sessions = list
            maybeAutoResume(list)
        }
    }

    /// Re-evaluates auto-resume when the snapshot becomes agent-authoritative:
    /// on a cold start the list often arrives before the flag flips.
    func observeSessionsSynced() async {
        for await _ in repository.sessionsSyncedStates() {
            maybeAutoResume(sessions)
        }
    }

    func observeEndedSessionNotices() async {
        for await _ in repository.endedSessionNotices() {
            notice = "That session has ended."
            repository.clearEndedSessionNotice()
        }
    }

    func observeConnection() async {
        for await state in agentEventBus.connectionStates() {
            isReconnecting = state == .connecting
        }
    }

    /// Seeds the scan list from REST (autoscan may be unconfigured — swallow),
    /// then mirrors bus-driven updates.
    func observeScans() async {
        _ = await autoScanRepository.refresh()
        for await scans in autoScanRepository.scans() {
            activeScans = scans
        }
    }

    /// Best-effort cold-start refresh (a session of a deleted/renamed type may
    /// predate the bus connection), then mirrors push-driven updates.
    func observeCustomTypes() async {
        try? await customAgentTypesRepository.refreshCustomTypes()
        for await types in customAgentTypesRepository.types() {
            customTypeNames = Self.typeNames(types)
        }
    }

    private static func typeNames(_ types: [CustomAgentTypeDto]) -> [String: String] {
        Dictionary(types.map { ("custom_\($0.id)", $0.name) }, uniquingKeysWith: { _, last in last })
    }

    func stopScan(_ projectName: String) async {
        do {
            try await autoScanRepository.stopScan(projectName: projectName)
        } catch {
            self.error = "Failed to stop auto scan: \(String(describing: error))"
        }
    }

    func refresh() async {
        await agentEventBus.forceReconnect()
        _ = await autoScanRepository.refresh()
    }

    func deleteSession(_ id: String, force: Bool) async {
        do {
            try await repository.deleteSession(id: id, force: force)
        } catch {
            self.error = "Failed to stop session: \(String(describing: error))"
        }
    }

    func clearError() {
        error = nil
    }

    func clearNotice() {
        notice = nil
    }

    /// Android `maybeAutoResume`: once a real snapshot arrives, open the stored
    /// last-used session if it is still running/starting; otherwise drop the id.
    ///
    /// Gated on `sessionsSynced` so a list left over from a dropped tunnel — which
    /// can still show a session as running after the agent restarted and lost it —
    /// never resumes into a session that no longer exists.
    private func maybeAutoResume(_ list: [SessionResponse]) {
        guard !didAttemptAutoResume, repository.sessionsSynced, !list.isEmpty else { return }
        didAttemptAutoResume = true
        guard let lastId = preferencesStore.getLastUsedSessionId() else { return }
        if let session = list.first(where: { $0.session_id == lastId }),
           session.state == "running" || session.state == "starting" {
            navBroadcaster.emit(lastId)
        } else {
            preferencesStore.setLastUsedSessionId(nil)
        }
    }
}

/// Display helpers shared by the list and its cards.
enum SessionDisplay {
    /// Custom kinds prefer the wire `kind_label` snapshot (survives
    /// rename/delete of the type), then the live registry name, then a
    /// generic "Custom" (Android `sessionKindLabel` parity).
    static func kindLabel(_ session: SessionResponse, customTypeNames: [String: String]) -> String {
        switch session.kind {
        case .claude: return "Claude Code"
        case .codex: return "Codex"
        case .antigravity: return "Antigravity"
        case .autoScan: return "Auto Scan"
        case .shell: return "Terminal"
        case .custom(let raw):
            return session.kind_label ?? customTypeNames[raw] ?? "Custom"
        }
    }

    /// The terminal header's label: the live OSC title while there is one,
    /// otherwise the kind. Deliberately never falls back to the session id —
    /// the cards may show a truncated id to disambiguate a list, a single
    /// screen's header has nothing to disambiguate against.
    static func headerLabel(_ session: SessionResponse, customTypeNames: [String: String]) -> String {
        if let title = session.title, !title.isBlank { return title }
        return kindLabel(session, customTypeNames: customTypeNames)
    }

    static func stateColor(_ state: String) -> Color {
        switch state {
        case "running": return MobaideColors.stateRunning
        case "starting": return MobaideColors.stateStarting
        case "stopping": return MobaideColors.stateStopping
        case "crashed": return MobaideColors.stateCrashed
        default: return MobaideColors.stateStopped
        }
    }

    static func attention(_ value: String?) -> (text: String, color: Color)? {
        switch value {
        case "awaiting_permission", "awaiting_os_permission":
            return ("Needs permission", MobaideColors.warningYellow)
        case "turn_finished":
            return ("Ready", MobaideColors.stateRunning)
        case "ended":
            return ("Ended", MobaideColors.stateStopped)
        default:
            return nil
        }
    }

    static func canStop(_ state: String) -> Bool {
        state == "running" || state == "starting"
    }
}
