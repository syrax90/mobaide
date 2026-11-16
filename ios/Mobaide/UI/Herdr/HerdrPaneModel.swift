import Foundation
import Observation

/// The history sheet: the pane's earlier output as text.
enum HerdrHistoryState: Equatable {
    case loading
    case loaded(text: String, truncated: Bool)
    case failed(String)
}

/// What the pane asks its screen to do, once.
enum HerdrPaneEvent: Equatable {
    case showMessage(String)
    /// The agent made the pane view again: the terminal attaches again.
    case reattach
    /// The pane is closed, or its herdr server stopped.
    case paneGone
}

/// The Herdr side of one pane screen (Android `HerdrPaneViewModel`): its title
/// and features from the dashboard state, the watch/control switch, the
/// history, and "seen". The terminal stream itself belongs to
/// `TerminalSessionModel`.
@MainActor
@Observable
final class HerdrPaneModel {
    private static let historyLines = 1000

    let serverId: String
    let paneId: String

    private(set) var title = "Herdr pane"
    private(set) var canControl = false
    /// Raw typing reaches the pane in watch mode. True until the server says otherwise.
    private(set) var typeInWatch = true
    private(set) var controlBusy = false
    private(set) var history: HerdrHistoryState?
    var showsHistory = false
    /// Set once per event; the screen handles and clears it.
    var event: HerdrPaneEvent?

    @ObservationIgnored private let repository: HerdrRepository
    @ObservationIgnored private let seenStore: HerdrSeenStore
    @ObservationIgnored private let foregroundSessionTracker: ForegroundSessionTracker
    @ObservationIgnored private let removeAlert: (String, String) -> Void
    /// Whether the pane is in control mode now; read when the screen goes away.
    @ObservationIgnored private var controlling = false
    /// Set while a re-open runs, so one ended view asks for one new one.
    @ObservationIgnored private var reopening = false

    init(
        serverId: String,
        paneId: String,
        repository: HerdrRepository,
        seenStore: HerdrSeenStore,
        foregroundSessionTracker: ForegroundSessionTracker,
        removeAlert: @escaping (String, String) -> Void = { _, _ in }
    ) {
        self.serverId = serverId
        self.paneId = paneId
        self.repository = repository
        self.seenStore = seenStore
        self.foregroundSessionTracker = foregroundSessionTracker
        self.removeAlert = removeAlert
        apply(repository.currentSnapshot)
    }

    private var trackerKey: String {
        ForegroundSessionTracker.herdrPaneKey(serverId: serverId, paneId: paneId)
    }

    func observe() async {
        // A cold open (a tapped notification) has no dashboard state yet.
        if repository.currentSnapshot == nil {
            Task { await repository.refresh() }
        }
        for await snapshot in repository.snapshots() {
            apply(snapshot)
        }
    }

    private func apply(_ snapshot: HerdrSnapshot?) {
        guard let found = HerdrDisplay.findPane(in: snapshot, serverId: serverId, paneId: paneId) else { return }
        title = HerdrDisplay.paneTitle(found.pane)
        canControl = found.server.features.control
        typeInWatch = found.server.features.type_in_watch
        // The pane is on screen: a finished turn it shows is seen.
        if found.pane.status == "done", let turn = found.pane.turn {
            seenStore.markSeen(
                key: HerdrSeenStore.key(agentId: repository.agentId, serverId: serverId, paneId: paneId),
                turn: turn
            )
        }
    }

    func markScreenVisible() {
        foregroundSessionTracker.setVisibleHerdrPane(trackerKey)
        // What the alert announced is on screen now.
        removeAlert(serverId, paneId)
    }

    func markScreenHidden() {
        foregroundSessionTracker.clearVisibleHerdrPane(trackerKey)
    }

    /// Whether this client controls the pane, as the terminal socket reports it.
    func modeChanged(isControl: Bool) {
        controlling = isControl
    }

    func setControl(_ control: Bool) {
        guard !controlBusy else { return }
        controlBusy = true
        Task {
            // With the answer, not with the mode change: a control that the
            // agent ends at once shows no change, and the button would stay off.
            defer { controlBusy = false }
            do {
                try await repository.setControl(serverId: serverId, paneId: paneId, control: control)
            } catch {
                event = .showMessage((error as? HerdrError)?.message ?? HerdrError.fallbackMessage)
            }
        }
    }

    /// The terminal reported `stopped`. The agent ends a pane view nobody
    /// watched for a while, and one whose herdr server restarted: ask for it
    /// again once. A pane that is really closed answers "gone".
    func sessionStopped() {
        guard !reopening else { return }
        reopening = true
        Task {
            do {
                _ = try await repository.openPane(serverId: serverId, paneId: paneId)
                event = .reattach
            } catch {
                let herdrError = error as? HerdrError
                event = herdrError?.isGone == true
                    ? .paneGone
                    : .showMessage(herdrError?.message ?? "Could not open the pane.")
            }
        }
    }

    func sessionRunning() {
        reopening = false
    }

    func openHistory() {
        showsHistory = true
        history = .loading
        Task {
            do {
                let read = try await repository.paneText(serverId: serverId, paneId: paneId, lines: Self.historyLines)
                history = .loaded(text: read.text, truncated: read.truncated)
            } catch {
                history = .failed((error as? HerdrError)?.message ?? "Could not read the pane.")
            }
        }
    }

    /// The screen is gone for good: give the pane back at once, without
    /// waiting for the socket to close.
    func leave() {
        markScreenHidden()
        guard controlling else { return }
        controlling = false
        Task { [repository, serverId, paneId] in
            try? await repository.setControl(serverId: serverId, paneId: paneId, control: false)
        }
    }
}
