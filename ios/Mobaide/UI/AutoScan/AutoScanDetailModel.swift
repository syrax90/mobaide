import Foundation

/// One phase in the timeline. Status ∈ pending | active | ok | failed | skipped.
struct PhaseState: Equatable, Sendable {
    var status: String = "pending"
}

/// A row in the "recent issues" list under the phase timeline.
struct IssueRow: Equatable, Sendable {
    let identifier: String
    let title: String
    let step: String
    let status: String
    let error: String?
}

/// A single rendered log line. `id` is a monotonic counter that is **never
/// recycled** and is **not** the event `seq` — one event (e.g. an idle
/// `scan_completed`) appends two lines, so the `List` key must be this id.
struct LogLine: Identifiable, Equatable, Sendable {
    let id: Int64
    let text: AttributedString
}

enum DetailConnection: String, Equatable, Sendable {
    case connecting, replaying, connected, disconnected
}

/// Per-workspace autoscan detail state (Android `AutoScanDetailViewModel`). It
/// reduces the dedicated-channel frame stream into UI state.
///
/// **Idempotency invariant.** Events arrive strictly monotonically in `seq` per
/// workspace over one socket, so the reduction cursor `lastSeq` skips `seq <=
/// lastSeq`: reconnect replays and snapshot/live overlaps are no-ops, and
/// replaying the same stream twice yields identical state. (Android instead
/// applied every event unconditionally — the duplicate-log bug.)
///
/// ANSI log lines are parsed into `AttributedString` **off the main actor** and
/// stored on the `LogLine`; the reduction stays sequential (each parse is
/// awaited before the next frame) so ordering is preserved.
@MainActor
@Observable
final class AutoScanDetailModel {
    static let phaseOrder = ["scan", "state", "label", "worktree", "spawn"]
    static let logRingMax = 2000
    private static let recentIssuesMax = 50

    let workspaceName: String

    private(set) var instance: AutoScanInstanceDto?
    private(set) var nextScanAt: String?
    private(set) var cycleNumber = 0
    private(set) var activePhase: String?
    private(set) var phases: [String: PhaseState]
    private(set) var recentIssues: [IssueRow] = []
    private(set) var logLines: [LogLine] = []
    private(set) var newLineCount = 0
    private(set) var isPausedAutoscroll = false
    private(set) var connection: DetailConnection = .connecting
    /// The scan was stopped (here or elsewhere) while the screen was open.
    private(set) var stopped = false

    private let repository: AutoScanRepository
    private var lastSeq: Int64 = 0
    private var nextLogId: Int64 = 0
    /// `started_at` of the last run this screen knew as running. A listed run
    /// that differs from it while the screen is stopped was started elsewhere.
    private var knownRun: Double?
    private var listedRun: Double?
    private var wasStopped = false
    private var frameTask: Task<Void, Never>?
    private var restartPending = false
    /// Bumped by a restart, so a reduction that awaited across it drops its old-run output.
    private var generation = 0

    init(workspaceName: String, repository: AutoScanRepository) {
        self.workspaceName = workspaceName
        self.repository = repository
        self.phases = Dictionary(uniqueKeysWithValues: Self.phaseOrder.map { ($0, PhaseState()) })
    }

    var orderedPhases: [(name: String, state: PhaseState)] {
        Self.phaseOrder.map { ($0, phases[$0] ?? PhaseState()) }
    }

    var statusText: String { stopped ? "stopped" : (instance?.status ?? connection.rawValue) }

    func errors() -> AsyncStream<AutoScanUiError> { repository.errors() }

    /// Consumes the detail frame stream until cancelled (the screen's `.task`
    /// cancels it on disappear, which disconnects the socket).
    ///
    /// `ws/autoscan` follows only the scans that ran when it connected, so a
    /// scan started again elsewhere is noticed on the Sessions list, and the
    /// frame stream is opened again from a fresh state.
    func run() async {
        let scans = repository.scans()
        let watcher = Task { [weak self] in
            for await list in scans {
                guard let self else { return }
                self.listedRun = list.first { $0.workspace_name == self.workspaceName }?.started_at
                self.followRestart()
            }
        }
        defer { watcher.cancel() }
        repeat {
            restartPending = false
            let resume = lastSeq > 0 ? [workspaceName: lastSeq] : [:]
            let stream = repository.observeDetailFrames(workspace: workspaceName, resume: resume)
            let consumer = Task { [weak self] in
                for await frame in stream {
                    // A cancelled stream can still hand over its buffered frames.
                    guard !Task.isCancelled else { break }
                    await self?.handle(frame)
                }
            }
            frameTask = consumer
            await withTaskCancellationHandler {
                await consumer.value
            } onCancel: {
                consumer.cancel()
            }
            frameTask = nil
        } while restartPending && !Task.isCancelled
    }

    private func followRestart() {
        let justStopped = stopped && !wasStopped
        wasStopped = stopped
        guard stopped else {
            // The snapshot names the running run; the list only fills in before it.
            if knownRun == nil { knownRun = listedRun }
            return
        }
        // Stopped before any run was known: a row listed now is the stopped run.
        if justStopped, knownRun == nil { knownRun = listedRun }
        guard let listedRun, listedRun != knownRun else { return }
        knownRun = listedRun
        restartDetail()
    }

    /// A new run's `seq` starts at 1, so the old cursor and log must go.
    private func restartDetail() {
        instance = nil
        nextScanAt = nil
        cycleNumber = 0
        activePhase = nil
        phases = Dictionary(uniqueKeysWithValues: Self.phaseOrder.map { ($0, PhaseState()) })
        recentIssues = []
        logLines = []
        newLineCount = 0
        connection = .connecting
        stopped = false
        wasStopped = false
        lastSeq = 0
        generation += 1
        guard let frameTask else { return }
        restartPending = true
        frameTask.cancel()
    }

    func runNow() async {
        do {
            let response = try await repository.runNow(workspaceName: workspaceName)
            nextScanAt = response.next_scan_at
        } catch let error as APIError where error.code == 404 {
            markStopped()
        } catch {
            // The repository already showed the error.
        }
    }

    func stopScan() async {
        do {
            try await repository.stopScan(workspaceName: workspaceName)
            markStopped()
        } catch let error as APIError where error.code == 404 {
            markStopped()
        } catch {
            // The repository already showed the error.
        }
    }

    private func markStopped() {
        stopped = true
        activePhase = nil
        nextScanAt = nil
        // Not inline: this runs inside a frame reduction, which must finish
        // with the old run's state before a restart clears it.
        Task { [weak self] in self?.followRestart() }
    }

    func setPaused(_ paused: Bool) {
        isPausedAutoscroll = paused
        if !paused { newLineCount = 0 }
    }

    func resumeAutoscroll() {
        isPausedAutoscroll = false
        newLineCount = 0
    }

    // MARK: - Reduction

    /// Reduces one frame into UI state. Internal (not private) so reduction
    /// tests can drive it without the WebSocket stack.
    func handle(_ frame: AutoScanFrame) async {
        switch frame {
        case .connected:
            connection = .connecting
        case .snapshot(let scans, let events):
            let match = scans.first { $0.workspace_name == workspaceName }
            if let match { knownRun = match.started_at }
            instance = match
            nextScanAt = match?.next_scan_at
            // Not listed: the PC no longer runs it. Listed again: started elsewhere.
            if match == nil { markStopped() } else { stopped = false; followRestart() }
            if let cycle = match?.cycle_number { cycleNumber = cycle }
            connection = .replaying
            let current = generation
            for event in events[workspaceName] ?? [] {
                guard current == generation else { return }
                await reduceEvent(event)
            }
        case .caughtUp:
            connection = .connected
        case .event(let event):
            guard event.workspaceName == workspaceName else { return }
            await reduceEvent(event)
        case .disconnected:
            connection = .disconnected
        }
    }

    private func reduceEvent(_ event: ParsedScanEvent) async {
        guard event.seq > lastSeq else { return }
        lastSeq = event.seq
        switch event.payload {
        case .phase(let phase, let cycle, let next, let issuesProcessed):
            handlePhase(phase: phase, cycle: cycle, nextScanAt: next, issuesProcessed: issuesProcessed)
        case .issue(let identifier, let title, let step, let status, let error):
            handleIssue(identifier: identifier, title: title, step: step, status: status, error: error)
        case .worktree(_, let path):
            appendLog(AttributedString("worktree: \(path)"))
        case .session(_, let sessionId, let pid):
            let suffix = pid.map { " (pid=\($0))" } ?? ""
            appendLog(AttributedString("session: \(sessionId)\(suffix)"))
        case .log(let line, _, let ansi):
            let current = generation
            let attributed = ansi
                ? await Task.detached { AnsiParser.parse(line) }.value
                : AttributedString(line)
            guard current == generation else { return }
            appendLog(attributed)
        case .unknown:
            break
        }
    }

    private func handlePhase(phase: String, cycle: Int, nextScanAt next: String?, issuesProcessed: Int) {
        switch phase {
        case "scan_started":
            cycleNumber = cycle
            activePhase = "scan"
            // Every non-scan phase resets to a fresh pending each cycle;
            // recentIssues is deliberately NOT cleared (idle scans keep the list).
            phases = Dictionary(uniqueKeysWithValues: Self.phaseOrder.map {
                ($0, PhaseState(status: $0 == "scan" ? "active" : "pending"))
            })
            appendLog(AttributedString("--- Scan #\(cycle) started ---"))
        case "scan_completed":
            let idle = issuesProcessed == 0
            nextScanAt = next
            activePhase = nil
            phases = phases.mapValues { state in
                if state.status == "active" { return PhaseState(status: "ok") }
                if idle, state.status == "pending" { return PhaseState(status: "skipped") }
                return state
            }
            appendLog(AttributedString("--- Scan #\(cycle) completed ---"))
            if idle {
                appendLog(AttributedString("no matching issues — skipping remaining stages"))
            }
        case "scan_failed":
            nextScanAt = next
            activePhase = nil
            phases = phases.mapValues { $0.status == "active" ? PhaseState(status: "failed") : $0 }
            appendLog(AttributedString("--- Scan #\(cycle) failed ---"))
        case "scan_stopped":
            markStopped()
            appendLog(AttributedString("--- Scan stopped ---"))
        default:
            break
        }
    }

    private func handleIssue(identifier: String, title: String, step: String, status: String, error: String?) {
        let phaseKey = Self.stepToPhase(step)
        let phaseStatus: String
        switch status {
        case "started": phaseStatus = "active"
        case "ok": phaseStatus = "ok"
        case "failed": phaseStatus = "failed"
        default: phaseStatus = "pending"
        }
        phases[phaseKey] = PhaseState(status: phaseStatus)
        recentIssues.append(IssueRow(identifier: identifier, title: title, step: step, status: status, error: error))
        if recentIssues.count > Self.recentIssuesMax {
            recentIssues.removeFirst()
        }
        if phaseStatus == "active" {
            activePhase = phaseKey
        }
        let errorSuffix = error.map { " [\($0)]" } ?? ""
        appendLog(AttributedString("\(identifier) \(step) \(status)\(errorSuffix)"))
    }

    private func appendLog(_ text: AttributedString) {
        if logLines.count >= Self.logRingMax {
            logLines.removeFirst()
        }
        logLines.append(LogLine(id: nextLogId, text: text))
        nextLogId += 1
        newLineCount = isPausedAutoscroll ? newLineCount + 1 : 0
    }

    private static func stepToPhase(_ step: String) -> String {
        switch step {
        case "state_update": return "state"
        case "label_removal": return "label"
        case "worktree_create": return "worktree"
        case "session_spawn": return "spawn"
        default: return step
        }
    }
}
