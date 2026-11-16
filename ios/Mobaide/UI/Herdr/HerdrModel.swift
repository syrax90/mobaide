import Foundation
import Observation

/// Feeds the session list's Herdr card. The card shows only while a herdr
/// server runs on the agent's computer: an agent without the dashboard, and a
/// computer without herdr, show nothing.
@MainActor
@Observable
final class HerdrCardModel {
    private(set) var snapshot: HerdrSnapshot?
    @ObservationIgnored private let repository: HerdrRepository
    @ObservationIgnored private let seenStore: HerdrSeenStore

    init(repository: HerdrRepository, seenStore: HerdrSeenStore) {
        self.repository = repository
        self.seenStore = seenStore
        snapshot = repository.currentSnapshot
    }

    var isVisible: Bool { !(snapshot?.servers.isEmpty ?? true) }

    private var rows: [HerdrPaneRow] {
        HerdrDisplay.paneRows(agentId: repository.agentId, snapshot: snapshot, seen: seenStore.seen)
    }

    var summary: String { HerdrDisplay.summary(rows) }
    var status: HerdrStatus { HerdrDisplay.rollup(rows) }

    func observe() async {
        for await snapshot in repository.snapshots() {
            self.snapshot = snapshot
        }
    }

    /// REST seed; `herdr.changed` keeps the state current afterwards.
    func refresh() async {
        await repository.refresh()
    }
}

/// Which sheet or question the dashboard shows. One at a time.
enum HerdrDialog: Identifiable, Equatable {
    case create(server: HerdrServer, workspaceId: String?)
    case rename(server: HerdrServer, target: HerdrTarget, id: String, current: String)
    case close(server: HerdrServer, target: HerdrTarget, id: String, label: String)
    case startAgent(server: HerdrServer, paneId: String)

    var id: String {
        switch self {
        case .create(let server, let workspaceId): return "create\u{0}\(server.server_id)\u{0}\(workspaceId ?? "")"
        case .rename(let server, let target, let id, _): return "rename\u{0}\(server.server_id)\u{0}\(target.rawValue)\u{0}\(id)"
        case .close(let server, let target, let id, _): return "close\u{0}\(server.server_id)\u{0}\(target.rawValue)\u{0}\(id)"
        case .startAgent(let server, let paneId): return "agent\u{0}\(server.server_id)\u{0}\(paneId)"
        }
    }
}

/// The pane the dashboard asks the screen to open.
struct HerdrOpenRequest: Equatable {
    let sessionId: String
    let serverId: String
    let paneId: String
}

/// The Herdr dashboard (Android `HerdrViewModel`). Its state is the
/// repository's snapshot, which `herdr.changed` keeps current, so an action
/// needs no refetch: the event that follows it redraws the tree.
@MainActor
@Observable
final class HerdrModel {
    private(set) var snapshot: HerdrSnapshot?
    /// The first read finished, so a missing snapshot means "nothing", not "not yet".
    private(set) var loaded = false
    /// An action is running: its sheet shows progress and blocks a second tap.
    private(set) var busy = false
    /// The agent's projects that exist: the folder choices of "New workspace".
    private(set) var projects: [ProjectInfo] = []
    var dialog: HerdrDialog? {
        didSet { if dialog != oldValue { actionError = nil } }
    }
    /// Why the action of the open sheet failed. Shown inside the sheet: a
    /// toast on the screen below would be covered by it.
    private(set) var actionError: String?
    var message: ToastMessage?
    /// Set once per pane to open; the screen navigates and clears it.
    var openRequest: HerdrOpenRequest?
    /// The `HerdrPaneRow.id` of the pane that is being opened. The open is a
    /// round trip to the agent: the row shows progress, and a second tap
    /// starts no second call.
    private(set) var openingKey: String?

    @ObservationIgnored private let repository: HerdrRepository
    @ObservationIgnored private let seenStore: HerdrSeenStore
    @ObservationIgnored private let projectsRepository: ProjectsRepository

    init(repository: HerdrRepository, seenStore: HerdrSeenStore, projectsRepository: ProjectsRepository) {
        self.repository = repository
        self.seenStore = seenStore
        self.projectsRepository = projectsRepository
        snapshot = repository.currentSnapshot
        projects = projectsRepository.currentProjects.filter(\.path_exists)
    }

    var servers: [HerdrServer] { snapshot?.servers ?? [] }

    /// Every pane, in tree order.
    var rows: [HerdrPaneRow] {
        HerdrDisplay.paneRows(agentId: repository.agentId, snapshot: snapshot, seen: seenStore.seen)
    }

    func observe() async {
        for await snapshot in repository.snapshots() {
            self.snapshot = snapshot
        }
    }

    func refresh() async {
        await repository.refresh()
        loaded = true
    }

    func observeProjects() async {
        // The bus snapshot may not have arrived on a cold open of this screen.
        _ = await projectsRepository.seedIfEmpty()
        for await list in projectsRepository.projects() {
            projects = list.filter(\.path_exists)
        }
    }

    func openPane(serverId: String, paneId: String) async {
        guard openingKey == nil else { return }
        openingKey = ForegroundSessionTracker.herdrPaneKey(serverId: serverId, paneId: paneId)
        defer { openingKey = nil }
        do {
            let sessionId = try await repository.openPane(serverId: serverId, paneId: paneId)
            openRequest = HerdrOpenRequest(sessionId: sessionId, serverId: serverId, paneId: paneId)
        } catch {
            show(error, fallback: "Could not open the pane.")
        }
    }

    /// Create a workspace, a tab or a split, then open the new pane: that is
    /// why it was made.
    func create(_ request: HerdrCreateRequest) async {
        await runAction {
            let created = try await self.repository.create(request)
            // The terminal exists though its agent did not start: say so, and open it.
            if let agentError = created.agent_error {
                self.message = ToastMessage(text: agentError, tone: .danger)
            }
            let paneId = created.pane_id
            let sessionId = try await self.repository.openPane(serverId: request.server_id, paneId: paneId)
            self.openRequest = HerdrOpenRequest(sessionId: sessionId, serverId: request.server_id, paneId: paneId)
        }
    }

    func startAgent(serverId: String, paneId: String, agent: String) async {
        await runAction {
            try await self.repository.startAgent(serverId: serverId, paneId: paneId, agent: agent)
        }
    }

    func rename(serverId: String, target: HerdrTarget, id: String, label: String) async {
        let trimmed = label.trimmingCharacters(in: .whitespacesAndNewlines)
        await runAction {
            try await self.repository.rename(serverId: serverId, target: target, id: id, label: trimmed)
        }
    }

    /// Herdr closes at once and cannot undo it; the screen asks before it calls this.
    func close(serverId: String, target: HerdrTarget, id: String) async {
        await runAction {
            try await self.repository.close(serverId: serverId, target: target, id: id)
        }
    }

    func markSeen(_ row: HerdrPaneRow) {
        guard let turn = row.pane.turn else { return }
        seenStore.markSeen(
            key: HerdrSeenStore.key(agentId: repository.agentId, serverId: row.server.server_id, paneId: row.pane.pane_id),
            turn: turn
        )
    }

    /// One action at a time: a second tap while one runs is dropped. A finished
    /// action closes its sheet; a failed one leaves it open with the reason.
    private func runAction(_ block: @escaping @MainActor () async throws -> Void) async {
        guard !busy else { return }
        busy = true
        actionError = nil
        defer { busy = false }
        do {
            try await block()
            dialog = nil
        } catch {
            // Only the create sheet is still open when its action fails: an
            // alert or a question closes at the tap.
            if case .create = dialog {
                actionError = (error as? HerdrError)?.message ?? HerdrError.fallbackMessage
            } else {
                show(error, fallback: HerdrError.fallbackMessage)
            }
        }
    }

    private func show(_ error: any Error, fallback: String) {
        let text = (error as? HerdrError)?.message ?? fallback
        message = ToastMessage(text: text, tone: .danger)
    }
}
