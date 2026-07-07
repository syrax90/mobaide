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
    private(set) var isReconnecting = false
    private(set) var error: String?

    private let repository: SessionRepository
    private let agentEventBus: AgentEventBus
    private let preferencesStore: PreferencesStore
    // `.latestClearable` so the one-shot auto-resume id survives the race where
    // `observeSessions` emits before the screen's `autoResumeEvents` task
    // subscribes, while `clearAutoResume()` (called by the screen after it
    // navigates) drops the cache so returning to the list does not replay it and
    // re-navigate. It fires at most once per model instance (didAttemptAutoResume).
    private let navBroadcaster = Broadcaster<String>(replay: .latestClearable)
    private var didAttemptAutoResume = false

    init(repository: SessionRepository, agentEventBus: AgentEventBus, preferencesStore: PreferencesStore) {
        self.repository = repository
        self.agentEventBus = agentEventBus
        self.preferencesStore = preferencesStore
        self.sessions = repository.currentSessions
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

    func observeConnection() async {
        for await state in agentEventBus.connectionStates() {
            isReconnecting = state == .connecting
        }
    }

    func refresh() async {
        await agentEventBus.forceReconnect()
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

    /// Android `maybeAutoResume`: once a real snapshot arrives, open the stored
    /// last-used session if it is still running/starting; otherwise drop the id.
    private func maybeAutoResume(_ list: [SessionResponse]) {
        guard !didAttemptAutoResume, !list.isEmpty else { return }
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
    static func kindLabel(_ kind: SessionKind) -> String {
        switch kind {
        case .claude: return "Claude Code"
        case .codex: return "Codex"
        case .antigravity: return "Antigravity"
        case .autoScan: return "Auto Scan"
        case .shell: return "Terminal"
        }
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
