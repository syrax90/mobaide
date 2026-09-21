import Foundation

struct ConflictScan: Equatable {
    /// Conflict blocks still open: lines starting a `<<<<<<<` marker.
    var conflicts = 0
    /// Any `<<<<<<<` or `>>>>>>>` line — what the agent refuses to mark resolved.
    var hasMarkers = false
    /// `|||||||` sections: the original lines are in the text.
    var hasBase = false
    /// More UTF-8 bytes than the agent's `byte_limit`.
    var tooLarge = false
}

/// One whole-file pick of the resolver, with its confirmation copy.
struct TakeChoice: Equatable, Identifiable {
    let side: String
    let label: String
    let title: String
    let body: String
    let confirm: String
    /// Removing the file is the destructive pick.
    let danger: Bool

    var id: String { side }
}

/// The conflict resolver: one conflicted file in an editor (Android
/// `GitConflictViewModel`).
///
/// The file is read once; nothing re-reads it behind the user's back, so the
/// agent's `file_changed` refusal is where an edit made in the session's
/// terminal surfaces, and the user then chooses between the file and the draft.
@MainActor
@Observable
final class GitConflictModel {
    enum State: Equatable {
        case loading
        case ready(GitConflictResponse)
        case agentOutdated
        case sessionGone
        case error(String)
    }

    /// Set once the conflict is settled or gone: the cover closes and the
    /// Changes list shows `message`.
    struct Outcome: Equatable {
        let message: String?
    }

    /// What a load does to the editor: fill it, or only adopt the file's new token.
    private enum LoadMode {
        case seed, token, tokenWithWarning
    }

    private(set) var state: State = .loading
    private(set) var scan = ConflictScan()
    private(set) var isDirty = false
    /// A request of this screen is in flight; the editor is read-only meanwhile.
    private(set) var isBusy = false
    /// The agent refused a write because the file moved on under the editor.
    private(set) var isStale = false
    /// "Keep my edits" was chosen: the next Apply overwrites the other change.
    private(set) var overwriteWarning = false
    /// The path left the conflict state without this screen's doing.
    private(set) var isGone = false
    private(set) var outcome: Outcome?
    var toast: ToastMessage?

    let sessionId: String
    let path: String
    let buffer = CodeTextBuffer()
    private let agentAPI: AgentAPI
    private var baseText = ""
    private var token: String?
    private var scanTask: Task<Void, Never>?

    static let scanDebounce: Duration = .milliseconds(150)

    init(sessionId: String, path: String, agentAPI: AgentAPI) {
        self.sessionId = sessionId
        self.path = path
        self.agentAPI = agentAPI
        buffer.onEdit = { [weak self] in self?.textDidChange() }
    }

    var fileName: String { GitStatusEntry.fileName(of: path) }

    var conflict: GitConflictResponse? {
        if case .ready(let conflict) = state { return conflict }
        return nil
    }

    var isEditable: Bool { conflict.map(GitConflictDisplay.isEditable) ?? false }
    // An oversized body is refused by the agent, and past a few MiB it would
    // not fit the relay's tunnel frame at all, so it is never sent.
    private var isSendable: Bool { isEditable && token != nil && !scan.tooLarge && !isBusy && !isGone }
    var canApply: Bool { isSendable && !scan.hasMarkers }
    var canSaveDraft: Bool { isSendable && isDirty }

    /// The screen's `.task`, which SwiftUI runs again every time the pushed
    /// diff is popped: only the first run may fill the editor, or coming back
    /// from *View diff* would replace an unsaved draft with the file.
    func load() async {
        guard conflict == nil else { return }
        await run { try await self.reload(.seed) }
    }


    /// Drop the draft for the file as it is now.
    func reloadFile() async {
        await run { try await self.reload(.seed) }
    }

    /// Keep the draft and adopt the file's new token: the next Apply overwrites
    /// the other change.
    func keepMyEdits() async {
        await run { try await self.reload(.tokenWithWarning) }
    }

    func apply() async {
        await save(markResolved: true)
    }

    func saveDraft() async {
        await save(markResolved: false)
    }

    func take(_ choice: TakeChoice) async {
        await mutate {
            try await self.agentAPI.takeSessionGitConflict(
                sessionId: self.sessionId, path: self.path, side: choice.side
            )
            self.outcome = Outcome(message: GitConflictDisplay.resolvedNotice(self.path))
        }
    }

    func startOver(withOriginalLines: Bool) async {
        await mutate {
            try await self.agentAPI.restoreSessionGitConflict(
                sessionId: self.sessionId,
                path: self.path,
                style: withOriginalLines
                    ? GitConflictRestoreRequest.styleDiff3
                    : GitConflictRestoreRequest.styleMerge,
                token: self.token
            )
            try await self.reload(.seed)
        }
    }

    /// Select the next `<<<<<<<` line and scroll to it.
    func jumpToNextConflict() {
        guard let range = GitConflictDisplay.nextConflictRange(in: buffer.text, after: buffer.caretOffset) else {
            return
        }
        buffer.select(range)
    }

    /// The editor changed; the marker scan trails the typing.
    func textDidChange() {
        scanTask?.cancel()
        scanTask = Task { [weak self] in
            try? await Task.sleep(for: Self.scanDebounce)
            guard !Task.isCancelled else { return }
            self?.rescan()
        }
    }

    func rescan() {
        let text = buffer.text
        scan = GitConflictDisplay.scan(text, byteLimit: conflict?.byte_limit ?? .max)
        isDirty = text != baseText
    }

    private func save(markResolved: Bool) async {
        guard let token else { return }
        let content = buffer.text
        await mutate {
            try await self.agentAPI.resolveSessionGitConflict(
                sessionId: self.sessionId,
                path: self.path,
                token: token,
                content: content,
                markResolved: markResolved
            )
            if markResolved {
                self.outcome = Outcome(message: GitConflictDisplay.resolvedNotice(self.path))
                return
            }
            // The draft is now the file: keep the text, take the file's new token.
            self.baseText = content
            self.isDirty = false
            try await self.reload(.token)
            self.toast = ToastMessage(text: GitConflictDisplay.draftSavedNotice(self.path), tone: .ok)
        }
    }

    private func mutate(_ block: () async throws -> Void) async {
        guard conflict != nil, !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            try await block()
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            let mapped = SessionGitError.map(error)
            switch mapped as? SessionGitError {
            case .git(let code, _)? where code == SessionGitError.codeFileChanged:
                isStale = true
            case .git(let code, _)? where code == SessionGitError.codeNotConflicted:
                isGone = true
            default:
                toast = ToastMessage(text: SessionGitCopy.mutationMessage(for: mapped), tone: .danger)
            }
        }
    }

    private func run(_ block: () async throws -> Void) async {
        if conflict == nil { state = .loading }
        guard !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            try await block()
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            let mapped = SessionGitError.map(error)
            let notConflicted: Bool
            if case .git(let code, _)? = mapped as? SessionGitError {
                notConflicted = code == SessionGitError.codeNotConflicted
            } else {
                notConflicted = false
            }
            // A draft outlives a failed reload: it may be the only copy of the work.
            if conflict != nil {
                if notConflicted {
                    isGone = true
                } else {
                    toast = ToastMessage(text: SessionGitCopy.errorMessage(for: mapped), tone: .danger)
                }
                return
            }
            switch mapped as? SessionGitError {
            case .agentOutdated?: state = .agentOutdated
            case .sessionGone?: state = .sessionGone
            default:
                if notConflicted {
                    outcome = Outcome(message: GitConflictDisplay.resolvedElsewhereNotice(path))
                } else {
                    state = .error(SessionGitCopy.errorMessage(for: mapped))
                }
            }
        }
    }

    private func reload(_ mode: LoadMode) async throws {
        let fresh = try await agentAPI.getSessionGitConflict(sessionId: sessionId, path: path)
        token = fresh.token
        let hadDraft = conflict != nil && GitConflictDisplay.isEditable(fresh)
        state = .ready(fresh)
        isStale = false
        if mode == .seed || !hadDraft {
            let text = fresh.content ?? ""
            baseText = text
            buffer.text = text
            scan = GitConflictDisplay.scan(text)
            isDirty = false
            overwriteWarning = false
        } else {
            overwriteWarning = mode == .tokenWithWarning
        }
    }
}

/// Derivations and copy for the conflict resolver, line for line Android
/// `GitConflictUiState.kt` and the web's `sessionGitConflictDisplay.ts`.
///
/// The wire says `ours` / `theirs` (index stages 2 and 3). The UI never does:
/// during a rebase "ours" is the branch being rebased *onto*, the opposite of
/// what the word suggests, so every string names a side by its label and role.
enum GitConflictDisplay {
    // The agent's marker test is `^(<{7,}|>{7,})(\s|$)` over bytes, and
    // Python's bytes-mode `\s` is exactly these six characters.
    private static let boundary: Set<UInt16> = [0x20, 0x09, 0x0A, 0x0D, 0x0C, 0x0B]

    private static func isMarker(_ line: ArraySlice<UInt16>, _ unit: UInt16) -> Bool {
        var run = 0
        for value in line {
            if value == unit { run += 1 } else { break }
        }
        guard run >= 7 else { return false }
        let next = line.index(line.startIndex, offsetBy: run)
        return next == line.endIndex || boundary.contains(line[next])
    }

    /// `(start, length)` of every line, split on `\n` only, in UTF-16 units.
    private static func lines(_ units: [UInt16]) -> [(start: Int, slice: ArraySlice<UInt16>)] {
        var out: [(Int, ArraySlice<UInt16>)] = []
        var start = 0
        for (index, unit) in units.enumerated() where unit == 0x0A {
            out.append((start, units[start..<index]))
            start = index + 1
        }
        out.append((start, units[start...]))
        return out
    }

    static func tooLargeToSaveNotice(_ byteLimit: Int) -> String {
        "Too large to save here (over \((byteLimit + 512) / 1024) KB)."
    }

    static func scan(_ text: String, byteLimit: Int = .max) -> ConflictScan {
        var scan = ConflictScan()
        scan.tooLarge = text.utf8.count > byteLimit
        for (_, line) in lines(Array(text.utf16)) {
            guard let first = line.first else { continue }
            switch first {
            case 0x3C where isMarker(line, 0x3C):
                scan.conflicts += 1
                scan.hasMarkers = true
            case 0x3E where isMarker(line, 0x3E):
                scan.hasMarkers = true
            case 0x7C where isMarker(line, 0x7C):
                scan.hasBase = true
            default:
                break
            }
        }
        return scan
    }

    /// The UTF-16 range of the next `<<<<<<<` line after `offset`, wrapping to
    /// the first; nil when none is left. A caret parked on a marker line moves
    /// on to the next.
    static func nextConflictRange(in text: String, after offset: Int) -> NSRange? {
        let starts = lines(Array(text.utf16))
            .filter { $0.slice.first == 0x3C && isMarker($0.slice, 0x3C) }
            .map { NSRange(location: $0.start, length: $0.slice.count) }
        return starts.first { $0.location > offset } ?? starts.first
    }

    static func conflictsLeftLabel(_ scan: ConflictScan) -> String {
        if scan.conflicts == 0 {
            return scan.hasMarkers ? "A conflict marker is left" : "No conflict markers left"
        }
        return scan.conflicts == 1 ? "1 conflict left" : "\(scan.conflicts) conflicts left"
    }

    static func sideName(_ conflict: GitConflictResponse, side: String) -> String {
        let ours = side == GitConflictResponse.sideOurs
        if let label = ours ? conflict.ours.label : conflict.theirs.label, !label.isEmpty { return label }
        return ours ? "Current branch" : "Incoming change"
    }

    static func sideRole(state: String, side: String) -> String {
        let ours = side == GitConflictResponse.sideOurs
        switch state {
        case GitRepoInfo.stateRebasing:
            return ours
                ? "the branch you are rebasing onto, plus your commits already replayed"
                : "your commit being replayed"
        case GitRepoInfo.stateMerging:
            return ours ? "the branch you are on" : "the branch being merged in"
        default:
            return ours ? "what you have checked out" : "the change being applied"
        }
    }

    /// What happened to the file, in the sides' own names.
    static func caption(_ conflict: GitConflictResponse) -> String {
        let ours = sideName(conflict, side: GitConflictResponse.sideOurs)
        let theirs = sideName(conflict, side: GitConflictResponse.sideTheirs)
        switch conflict.code {
        case "UU": return "Both sides changed this file."
        case "AA": return "Both sides added this file."
        case "DU": return "\(ours) deleted this file; \(theirs) changed it."
        case "UD": return "\(ours) changed this file; \(theirs) deleted it."
        case "DD": return "Both sides deleted this file."
        case "AU": return "Only \(ours) has this file."
        case "UA": return "Only \(theirs) has this file."
        default: return "The two sides disagree about this file."
        }
    }

    /// How to read the markers: which region of a conflict block is which side.
    static func legend(_ conflict: GitConflictResponse, hasBase: Bool) -> [String] {
        let oursSide = GitConflictResponse.sideOurs
        let theirsSide = GitConflictResponse.sideTheirs
        let ours = "\(sideName(conflict, side: oursSide)) — \(sideRole(state: conflict.state, side: oursSide))."
        let theirs = "\(sideName(conflict, side: theirsSide)) — \(sideRole(state: conflict.state, side: theirsSide))."
        guard hasBase else {
            return ["Between <<<<<<< and =======: \(ours)", "Between ======= and >>>>>>>: \(theirs)"]
        }
        return [
            "Between <<<<<<< and |||||||: \(ours)",
            "Between ||||||| and =======: the original lines, before either change.",
            "Between ======= and >>>>>>>: \(theirs)",
        ]
    }

    static func isEditable(_ conflict: GitConflictResponse) -> Bool {
        conflict.kind == GitConflictResponse.kindText && conflict.content != nil
    }

    /// Why a conflict cannot be edited here; nil for an editable one.
    static func kindNotice(_ conflict: GitConflictResponse) -> String? {
        switch conflict.kind {
        case GitConflictResponse.kindText:
            return nil
        case GitConflictResponse.kindTooLarge:
            let limit = (conflict.byte_limit + 512) / 1024
            return "This file is too large to edit here (over \(limit) KB). Pick a side, or resolve it in the terminal."
        case GitConflictResponse.kindBinary:
            return "This is a binary file — pick a side."
        case GitConflictResponse.kindEncoding:
            return "This file is not UTF-8 text — pick a side, or resolve it in the terminal."
        case GitConflictResponse.kindMissing:
            return "The file is not in the working tree — pick a side."
        case GitConflictResponse.kindSymlink:
            return "This is a symbolic link — pick a side."
        case GitConflictResponse.kindSubmodule:
            return "Submodule conflicts are resolved in the terminal."
        default:
            return "This conflict can't be resolved here — use the terminal."
        }
    }

    private static let takeableKinds: Set<String> = [
        GitConflictResponse.kindText,
        GitConflictResponse.kindTooLarge,
        GitConflictResponse.kindBinary,
        GitConflictResponse.kindEncoding,
        GitConflictResponse.kindMissing,
        GitConflictResponse.kindSymlink,
    ]

    /// Whole-file picks exist for every known shape but a submodule or a
    /// directory in the way.
    static func canTakeSide(_ conflict: GitConflictResponse) -> Bool {
        takeableKinds.contains(conflict.kind)
    }

    /// `git checkout --merge` needs both sides' versions to rebuild the markers.
    static func canStartOver(_ conflict: GitConflictResponse) -> Bool {
        isEditable(conflict) && conflict.ours.present && conflict.theirs.present
    }

    /// The whole-file picks. Both sides present: use one or the other.
    /// Otherwise a side that lacks the file means "delete" and the one that has
    /// it "keep"; when neither has it there is a single way out.
    static func takeChoices(_ conflict: GitConflictResponse) -> [TakeChoice] {
        guard canTakeSide(conflict) else { return [] }
        let file = GitStatusEntry.fileName(of: conflict.path)
        let oursPresent = conflict.ours.present
        let theirsPresent = conflict.theirs.present
        func choice(_ side: String) -> TakeChoice {
            let name = sideName(conflict, side: side)
            let present = side == GitConflictResponse.sideOurs ? oursPresent : theirsPresent
            if oursPresent && theirsPresent {
                return TakeChoice(
                    side: side,
                    label: "Use \(name) for the whole file",
                    title: "Use \(name) for the whole file?",
                    body: "Every conflict in \(file) is settled with this side and the file is marked resolved. "
                        + "Edits made to it are discarded.",
                    confirm: "Use this side",
                    danger: false
                )
            }
            if present {
                return TakeChoice(
                    side: side,
                    label: "Keep the file (\(name))",
                    title: "Keep \(file)?",
                    body: "The file stays as \(name) has it and is marked resolved.",
                    confirm: "Keep file",
                    danger: false
                )
            }
            return TakeChoice(
                side: side,
                label: oursPresent || theirsPresent ? "Delete the file (\(name))" : "Delete the file",
                title: "Delete \(file)?",
                body: "The file is removed, as on \(name), and the conflict is marked resolved.",
                confirm: "Delete file",
                danger: true
            )
        }
        if !oursPresent && !theirsPresent { return [choice(GitConflictResponse.sideOurs)] }
        return [choice(GitConflictResponse.sideOurs), choice(GitConflictResponse.sideTheirs)]
    }

    static func startOverPrompt(_ path: String) -> (title: String, body: String) {
        (
            "Start over?",
            "\(GitStatusEntry.fileName(of: path)) is rebuilt from both sides. Every edit made to it since "
                + "the conflict — here or in the terminal — is discarded."
        )
    }

    static let startOverLabel = "Start over"
    static let startOverWithBaseLabel = "Start over with original lines"

    static func restorePrompt(_ path: String) -> (title: String, body: String, confirm: String) {
        (
            "Restore the conflict?",
            "\(GitStatusEntry.fileName(of: path)) goes back to its conflicted state. "
                + "The resolution you applied is discarded.",
            "Restore conflict"
        )
    }

    static func discardEditsPrompt(_ path: String) -> (title: String, body: String) {
        ("Discard your edits?", "Your changes to \(GitStatusEntry.fileName(of: path)) have not been saved.")
    }

    static func resolvedNotice(_ path: String) -> String { "Resolved \(path)." }

    static func draftSavedNotice(_ path: String) -> String { "Saved \(path) — the conflict is still open." }

    static func restoredNotice(_ path: String) -> String { "Restored the conflict in \(path)." }

    static func fileChangedNotice(_ path: String) -> String {
        "\(GitStatusEntry.fileName(of: path)) changed on the agent machine since you opened it."
    }

    static let keepEditsWarning = "Applying will overwrite the other change."

    static func resolvedElsewhereNotice(_ path: String) -> String {
        "\(GitStatusEntry.fileName(of: path)) is no longer in conflict."
    }
}
