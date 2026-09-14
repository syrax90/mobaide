import Foundation
import Observation
import UIKit

/// One rendered row of the Diff screen; the agent's hunks flattened for a
/// lazy stack.
struct DiffRow: Equatable, Identifiable {
    enum Kind: Equatable {
        case hunkHeader(range: String, heading: String)
        case line(type: String, old: Int?, new: Int?, text: String)
        case noNewline
        case truncated(shown: Int, total: Int)
        case notice(String)
    }

    let id: String
    let kind: Kind
}

/// Column widths in points, measured once per load in the code font (Android
/// `DiffMetrics`).
struct DiffMetrics: Equatable {
    /// One monospaced cell.
    let cell: CGFloat
    let gutterWidth: CGFloat
    /// The widest line, shared by every line row so the rows scroll together.
    let textWidth: CGFloat
}

/// Per-file diff state (Android `GitDiffViewModel`).
@MainActor
@Observable
final class GitDiffModel {
    enum State: Equatable {
        case loading
        case ready(diff: GitDiffResponse, rows: [DiffRow], metrics: DiffMetrics)
        case agentOutdated
        case sessionGone
        case error(String)
    }

    /// Identity of one load; the screen's `.task(id:)` restarts — and cancels
    /// the in-flight request — whenever it changes.
    struct LoadKey: Hashable {
        let context: Int
        let reloads: Int
    }

    static let contextDefault = 3
    /// The agent's context ceiling, at which an untruncated diff is the whole
    /// file.
    static let contextFull = 5_000
    static let contextChoices = [(contextDefault, "Changes"), (contextFull, "Full file")]

    private(set) var state: State = .loading
    private(set) var isLoading = false
    var context = GitDiffModel.contextDefault
    private(set) var reloads = 0

    let sessionId: String
    let path: String
    let scope: String
    let origPath: String?
    private let agentAPI: AgentAPI
    private var loadGeneration = 0

    init(sessionId: String, path: String, scope: String, origPath: String?, agentAPI: AgentAPI) {
        self.sessionId = sessionId
        self.path = path
        self.scope = scope
        self.origPath = origPath
        self.agentAPI = agentAPI
    }

    var loadKey: LoadKey { LoadKey(context: context, reloads: reloads) }

    var fileName: String { GitStatusEntry.fileName(of: path) }

    func retry() {
        reloads += 1
    }

    func load() async {
        loadGeneration += 1
        let generation = loadGeneration
        isLoading = true
        defer { if generation == loadGeneration { isLoading = false } }
        // A retry must visibly restart; a stale `ready` stays up (as
        // "refreshing") while a new context loads.
        if case .error = state {
            state = .loading
        }
        do {
            let diff = try await agentAPI.getSessionGitDiff(
                sessionId: sessionId,
                path: path,
                scope: scope,
                context: context,
                origPath: origPath
            )
            guard generation == loadGeneration else { return }
            let rows = GitDiffDisplay.rows(for: diff)
            state = .ready(
                diff: diff,
                rows: rows,
                metrics: GitDiffDisplay.metrics(rows: rows, gutterDigits: GitDiffDisplay.gutterDigits(for: diff))
            )
        } catch {
            guard generation == loadGeneration, !SessionGitError.isCancellation(error) else { return }
            let mapped = SessionGitError.map(error)
            switch mapped as? SessionGitError {
            case .agentOutdated?: state = .agentOutdated
            case .sessionGone?: state = .sessionGone
            default: state = .error(SessionGitCopy.errorMessage(for: mapped))
            }
        }
    }
}

/// Pure derivations for the Diff screen (Android `GitDiffUiState.kt`).
enum GitDiffDisplay {
    /// Columns kept per rendered line. The agent caps lines and bytes, never
    /// columns, so a minified line is cut here rather than laid out whole.
    static let maxRenderedColumns = 2_000
    private static let lineCutMark = "…"
    private static let tabWidth = 4

    static func rows(for diff: GitDiffResponse) -> [DiffRow] {
        var rows: [DiffRow] = []
        switch diff.kind {
        case GitDiffResponse.kindBinary:
            rows.append(DiffRow(id: "notice", kind: .notice("Binary file — no preview")))
        case GitDiffResponse.kindTooLarge:
            rows.append(DiffRow(
                id: "notice",
                kind: .notice("File too large to preview (\(diff.byte_limit / (1024 * 1024)) MiB limit)")
            ))
        case GitDiffResponse.kindMissing:
            rows.append(DiffRow(id: "notice", kind: .notice("Nothing to show for this file")))
        default:
            var shown = 0
            for (h, hunk) in diff.hunks.enumerated() {
                // A hunk with no lines in a truncated diff is overflow past the
                // cap, not content.
                if diff.truncated && hunk.lines.isEmpty { continue }
                rows.append(DiffRow(
                    id: "h\(h)",
                    kind: .hunkHeader(
                        range: "@@ -\(hunk.old_start),\(hunk.old_lines) +\(hunk.new_start),\(hunk.new_lines) @@",
                        heading: hunk.heading
                    )
                ))
                for (i, line) in hunk.lines.enumerated() {
                    rows.append(DiffRow(
                        id: "h\(h):l\(i)",
                        kind: .line(type: line.t, old: line.old, new: line.new, text: renderableText(line.text))
                    ))
                    shown += 1
                    if line.no_newline {
                        rows.append(DiffRow(id: "h\(h):l\(i):nn", kind: .noNewline))
                    }
                }
            }
            if diff.truncated {
                rows.append(DiffRow(id: "truncated", kind: .truncated(shown: shown, total: diff.total_lines)))
            } else if rows.isEmpty {
                rows.append(DiffRow(id: "notice", kind: .notice(emptyDiffNotice(diff))))
            }
        }
        return rows
    }

    /// Tabs become spaces so every row shares one column grid; a carriage
    /// return becomes a visible ␍ so a CRLF file reads as one rather than
    /// hiding a control character; anything past `maxRenderedColumns` is cut.
    static func renderableText(_ text: String) -> String {
        var expanded = text
        if text.contains("\t") || text.contains("\r") {
            expanded = text
                .replacingOccurrences(of: "\t", with: String(repeating: " ", count: tabWidth))
                .replacingOccurrences(of: "\r", with: "␍")
        }
        guard expanded.count > maxRenderedColumns else { return expanded }
        return String(expanded.prefix(maxRenderedColumns)) + lineCutMark
    }

    static func gutterDigits(for diff: GitDiffResponse) -> Int {
        var highest = 0
        for hunk in diff.hunks {
            highest = max(highest, hunk.old_start + hunk.old_lines, hunk.new_start + hunk.new_lines)
        }
        return max(2, String(highest).count)
    }

    /// Candidates always measured.
    static let widthMeasureFloor = 8
    /// Candidates measured at most.
    static let widthMeasureLimit = 16
    private static let cellSample = 100

    /// A line's estimated width in cells, counted per UTF-16 unit like
    /// Android: Latin, Greek and Cyrillic, which the code font draws one cell
    /// wide, count one; every other unit counts two (an emoji pair, four).
    static func weightedLength(_ text: String) -> Int {
        text.utf16.reduce(0) { $0 + ($1 <= 0x024F || (0x0370...0x04FF).contains($1) ? 1 : 2) }
    }

    /// UTF-16 units the code font draws exactly one cell wide: a lower bound on
    /// a line's width in cells.
    static func narrowLength(_ text: String) -> Int {
        text.utf16.reduce(0) { $0 + ((0x20...0x024F).contains($1) || (0x0370...0x04FF).contains($1) ? 1 : 0) }
    }

    /// The line with the most one-cell units, or nil without line rows.
    static func narrowCandidate(_ rows: [DiffRow]) -> String? {
        var best: (text: String, length: Int)?
        for row in rows {
            guard case .line(_, _, _, let text) = row.kind else { continue }
            let length = narrowLength(text)
            if length > (best?.length ?? -1) { best = (text, length) }
        }
        return best?.text
    }

    /// Line texts, widest estimate first.
    static func widthCandidates(_ rows: [DiffRow], limit: Int = widthMeasureLimit) -> [String] {
        let texts: [(text: String, weight: Int)] = rows.compactMap { row in
            guard case .line(_, _, _, let text) = row.kind else { return nil }
            return (text, weightedLength(text))
        }
        return texts.sorted { $0.weight > $1.weight }.prefix(limit).map(\.text)
    }

    /// The widest line plus one cell of slack, as laid out in `font`. The top
    /// `widthMeasureFloor` candidates are always measured; later ones only
    /// while their estimate could still beat the widest measured line. The
    /// `narrowCandidate` is measured as well, so a widest line of Latin, Greek
    /// or Cyrillic that overweighted scripts pushed out of the candidates is
    /// never cut.
    static func metrics(
        rows: [DiffRow],
        gutterDigits: Int,
        font: UIFont = MobaideTypography.codeUIFont
    ) -> DiffMetrics {
        func width(_ text: String) -> CGFloat {
            (text as NSString).size(withAttributes: [.font: font]).width
        }
        let exactCell = width(String(repeating: "0", count: cellSample)) / CGFloat(cellSample)
        var widest: CGFloat = 0
        for (index, text) in widthCandidates(rows).enumerated() {
            if index >= widthMeasureFloor, CGFloat(weightedLength(text)) * exactCell <= widest + 0.5 { break }
            widest = max(widest, width(text))
        }
        if let narrow = narrowCandidate(rows) {
            widest = max(widest, width(narrow))
        }
        let cell = ceil(exactCell)
        return DiffMetrics(cell: cell, gutterWidth: cell * CGFloat(gutterDigits), textWidth: ceil(widest) + cell)
    }

    /// `total_lines` is only a lower bound when the agent's byte cap cut git
    /// off (PROTOCOL.md), so the copy never claims an exact size.
    static func truncatedNotice(shown: Int, total: Int) -> String {
        total > shown
            ? "Showing the first \(shown) of at least \(total) lines"
            : "Truncated after \(shown) lines — the full diff is longer"
    }

    static func hunkHeaderText(range: String, heading: String) -> String {
        heading.isEmpty ? range : "\(range)  \(heading)"
    }

    static func changeLabel(_ change: String) -> String {
        change.replacingOccurrences(of: "_", with: " ")
    }

    static func changeTone(_ change: String) -> PixelTone {
        switch change {
        case GitDiffResponse.changeAdded: return .ok
        case GitDiffResponse.changeDeleted: return .danger
        case GitDiffResponse.changeRenamed, GitDiffResponse.changeCopied: return .accent
        default: return .warning
        }
    }

    static func lineTone(_ type: String) -> PixelTone? {
        switch type {
        case GitDiffLine.typeAdd: return .ok
        case GitDiffLine.typeDelete: return .danger
        default: return nil
        }
    }

    static func marker(_ type: String) -> String {
        switch type {
        case GitDiffLine.typeAdd: return "+"
        case GitDiffLine.typeDelete: return "-"
        default: return " "
        }
    }

    /// `+x −y` only means something for a text or submodule diff.
    static func showsCounts(_ diff: GitDiffResponse) -> Bool {
        diff.kind == GitDiffResponse.kindText || diff.kind == GitDiffResponse.kindSubmodule
    }

    private static let contextlessChanges: Set<String> = [
        GitDiffResponse.changeAdded,
        GitDiffResponse.changeDeleted,
        GitDiffResponse.changeTypeChanged,
    ]

    /// Whether the context choice can change what is shown: only a text diff
    /// with hunks of a file present on both sides has unchanged lines to widen.
    /// An added or deleted file is one all-add or all-del hunk, and a type
    /// change is both.
    static func showsContextChoices(_ diff: GitDiffResponse) -> Bool {
        guard diff.kind == GitDiffResponse.kindText, !diff.hunks.isEmpty else { return false }
        guard let change = diff.change else { return true }
        return !contextlessChanges.contains(change)
    }

    static func modeChange(_ diff: GitDiffResponse) -> String? {
        guard let old = diff.old_mode, let new = diff.new_mode, old != new else { return nil }
        return "\(old) → \(new)"
    }

    private static func emptyDiffNotice(_ diff: GitDiffResponse) -> String {
        modeChange(diff).map { "Mode changed only: \($0)" } ?? "No text changes"
    }
}
