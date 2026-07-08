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

/// Per-project autoscan detail state (Android `AutoScanDetailViewModel`). It
/// reduces the dedicated-channel frame stream into UI state.
///
/// **Idempotency invariant.** Events arrive strictly monotonically in `seq` per
/// project over one socket, so the reduction cursor `lastSeq` skips `seq <=
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

    let projectName: String

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

    private let repository: AutoScanRepository
    private var lastSeq: Int64 = 0
    private var nextLogId: Int64 = 0

    init(projectName: String, repository: AutoScanRepository) {
        self.projectName = projectName
        self.repository = repository
        self.phases = Dictionary(uniqueKeysWithValues: Self.phaseOrder.map { ($0, PhaseState()) })
    }

    var orderedPhases: [(name: String, state: PhaseState)] {
        Self.phaseOrder.map { ($0, phases[$0] ?? PhaseState()) }
    }

    var statusText: String { instance?.status ?? connection.rawValue }

    func errors() -> AsyncStream<AutoScanUiError> { repository.errors() }

    /// Consumes the detail frame stream until cancelled (the screen's `.task`
    /// cancels it on disappear, which disconnects the socket).
    func run() async {
        let resume = lastSeq > 0 ? [projectName: lastSeq] : [:]
        let stream = repository.observeDetailFrames(project: projectName, resume: resume)
        for await frame in stream {
            await handle(frame)
        }
    }

    func runNow() async {
        guard let response = try? await repository.runNow(projectName: projectName) else { return }
        nextScanAt = response.next_scan_at
    }

    func stopScan() async {
        try? await repository.stopScan(projectName: projectName)
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
            let match = scans.first { $0.project_name == projectName }
            instance = match
            nextScanAt = match?.next_scan_at
            if let cycle = match?.cycle_number { cycleNumber = cycle }
            connection = .replaying
            for event in events[projectName] ?? [] {
                await reduceEvent(event)
            }
        case .caughtUp:
            connection = .connected
        case .event(let event):
            guard event.projectName == projectName else { return }
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
            let attributed = ansi
                ? await Task.detached { AnsiParser.parse(line) }.value
                : AttributedString(line)
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
        case "scan_stopped":
            activePhase = nil
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
