import Foundation
import Observation

/// One group of the Changes list (Conflicts / Staged / Changes / Untracked).
struct GitChangesSection: Equatable, Identifiable {
    let group: String
    let title: String
    let rows: [GitChangesRow]

    var id: String { group }
}

struct GitChangesRow: Equatable, Identifiable {
    /// `group:occurrence:path`, occurrence ahead of the path so no filename can
    /// forge another row's key (two names whose invalid bytes both decode to
    /// U+FFFD collide on the bare path).
    let id: String
    let entry: GitStatusEntry
}

/// The confirmation Discard always goes through; never one tap.
struct DiscardPrompt: Equatable {
    let rows: [GitChangesRow]
    let title: String
    let body: String
}

enum CommitContextState: Equatable {
    case loading
    /// `author` nil: git cannot say who commits; `head` nil: nothing to amend.
    case ready(author: GitCommitAuthor?, head: GitCommitHead?)
    case failed(String)
}

struct CommitError: Equatable {
    let message: String
    let output: String?
}

struct CommitSheetState: Equatable {
    var context: CommitContextState = .loading
    var isCommitting = false
    var error: CommitError?
}

/// The fetch / pull / push this screen follows: `job` is the latest snapshot
/// (nil until the first one lands), `lost` that the agent no longer knows the
/// id.
struct GitJobBanner: Equatable {
    let jobId: String
    var job: GitJobResponse?
    var lost = false

    var running: Bool { !lost && (job?.running ?? true) }
    var failed: Bool { lost || job?.ok == false }

    var text: String {
        if lost { return GitChangesDisplay.jobLostCopy }
        guard let job else { return "Syncing…" }
        return GitChangesDisplay.jobNotice(job)
    }
}

struct SyncSheetState: Equatable {
    /// The start request is in flight — the one moment the sheet cannot be dismissed.
    var isStarting = false
    var error: String?
}

enum SyncFollowUp: Equatable {
    case pullRebase, pullMerge, forcePush
}

/// The confirmation every follow-up goes through.
struct SyncPrompt: Equatable {
    let followUp: SyncFollowUp
    let title: String
    let body: String
    let confirm: String
}

/// Where the rebase sheet's picker stands: no project types the ref instead.
enum RebaseTargets: Equatable {
    case loading
    case ready([GitItem])
    /// No project, or the list failed to load: the sheet asks for the ref as text.
    case typed
}

struct RebaseSheetState: Equatable {
    var targets: RebaseTargets = .loading
    var isStarting = false
    var error: CommitError?
}

/// The two rebase steps that go through a confirmation.
enum RebasePromptStep: Equatable {
    case skip, abort
}

struct RebasePrompt: Equatable {
    let step: RebasePromptStep
    let title: String
    let body: String
    let confirm: String
}

/// Session-scoped Changes screen state (Android `GitChangesViewModel` +
/// `GitRepository.observeSessionGitStatus`).
///
/// Polling, not just push: `git.changed` only fires for the app's own git
/// mutations, so a commit typed into the PTY — the whole point of the viewer —
/// never announces itself. `run()` therefore polls while the screen is shown
/// and the app is not in the background; each request carries the last `etag`,
/// so an idle repo costs a tiny `unchanged` round trip. The feed ends after an
/// old-agent 404 or a session-not-found 404: neither answer can change, so
/// there is nothing left to poll — a later pull-to-refresh answers once but
/// does not re-arm the poll.
@MainActor
@Observable
final class GitChangesModel {
    enum State: Equatable {
        case loading
        case ready(status: GitStatusResponse, repo: GitRepoInfo, sections: [GitChangesSection])
        case unavailable(reason: String, cwd: String?)
        case agentOutdated
        case sessionGone
        case error(String)
    }

    static let pollInterval: Duration = .seconds(10)
    /// The reconcile cadence of a running fetch / pull / push: the socket
    /// frames are the fast path, this is what survives a dropped
    /// `git.job.done`, a background stretch, or a job another client started.
    static let jobPollInterval: Duration = .seconds(3)
    /// How long a successful job's banner stays before it clears itself.
    static let jobResultDismissDelay: Duration = .seconds(6)

    private(set) var state: State = .loading
    /// A failed refresh over content already shown; cleared by the next success.
    private(set) var staleError: String?
    private(set) var isLoading = false
    /// Which mutating controls the agent advertises; none for an older agent.
    private(set) var capabilities = GitCapabilities.none
    /// Row ids ticked in edit mode, bound to the `List` selection.
    var selection: Set<String> = []
    /// A mutation is in flight: every control that would start another waits.
    private(set) var isMutating = false
    var discardPrompt: DiscardPrompt?
    var commitSheet: CommitSheetState?
    var syncSheet: SyncSheetState?
    /// The follow-up confirmation; its `isPresented` setter clears only this.
    var syncPrompt: SyncPrompt?
    var rebaseSheet: RebaseSheetState?
    /// Skip or Abort waiting for its confirmation; the `isPresented` setter clears only this.
    var rebasePrompt: RebasePrompt?
    var toast: ToastMessage?
    /// The fetch / pull / push this screen follows, until its result is
    /// dismissed. Adopted from wherever it surfaces — this screen's own start,
    /// a socket frame for a job another client started, or the id a
    /// `409 job_running` names.
    private(set) var job: GitJobBanner?
    /// Consumed by the screen's first `.task`: the terminal menu's Commit…
    /// lands here with the sheet already up.
    private(set) var openCommitOnAppear: Bool
    /// The same for the menu's Fetch / Pull / Push….
    private(set) var openSyncOnAppear: Bool
    /// The same for the menu's Rebase….
    private(set) var openRebaseOnAppear: Bool

    let sessionId: String
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let projectName: @MainActor () -> String?
    private let sleeper: any Sleeper

    /// The etag cache. Lives as long as the screen is on the navigation stack,
    /// so a return from the Diff screen costs an `unchanged` round trip.
    private var cached: GitStatusResponse?
    private var loadGeneration = 0
    /// The rows a confirmation dialog acts on, kept apart from `discardPrompt`:
    /// SwiftUI writes the dialog's `isPresented` binding on dismissal, which
    /// can clear the prompt before the confirm action runs.
    private var pendingDiscard: [GitChangesRow] = []
    /// The follow-up a confirmation acts on, kept apart from `syncPrompt` for
    /// the same reason.
    private var pendingFollowUp: SyncFollowUp?
    /// The rebase step a confirmation acts on, kept apart from `rebasePrompt`
    /// for the same reason.
    private var pendingRebaseStep: RebasePromptStep?
    /// A dismissed job's id, so a late frame does not re-adopt it.
    private var dismissedJobId: String?

    init(
        sessionId: String,
        agentAPI: AgentAPI,
        agentEventBus: AgentEventBus,
        projectName: @escaping @MainActor () -> String?,
        sleeper: any Sleeper = ContinuousSleeper(),
        openCommit: Bool = false,
        openSync: Bool = false,
        openRebase: Bool = false
    ) {
        self.sessionId = sessionId
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.projectName = projectName
        self.sleeper = sleeper
        self.openCommitOnAppear = openCommit
        self.openSyncOnAppear = openSync
        self.openRebaseOnAppear = openRebase
    }

    var repo: GitRepoInfo? {
        if case .ready(_, let repo, _) = state { return repo }
        return nil
    }

    /// A mutation is in flight, or a fetch / pull / push owns the repository
    /// (the agent answers `job_running` to every write meanwhile); Cancel,
    /// refresh and the Sync button stay live.
    var isBusy: Bool { isMutating || job?.running == true }

    /// The `.task(id:)` key of the reconcile loop: it restarts when the job
    /// changes or finishes and when the app leaves the background.
    func jobReconcileKey(active: Bool) -> String {
        "\(job?.jobId ?? "")|\(job?.running == true)|\(active)"
    }

    var sections: [GitChangesSection] {
        if case .ready(_, _, let sections) = state { return sections }
        return []
    }

    var stagedCount: Int {
        if case .ready(let status, _, _) = state { return status.counts?.staged ?? 0 }
        return 0
    }

    var conflictCount: Int {
        if case .ready(let status, _, _) = state { return status.counts?.conflicts ?? 0 }
        return 0
    }

    var status: GitStatusResponse? {
        if case .ready(let status, _, _) = state { return status }
        return nil
    }

    /// Whether any shown row has a Stage / Unstage / Discard to offer.
    var hasActionableRows: Bool {
        sections.contains { section in
            section.rows.contains { GitChangesDisplay.rowActions($0.entry, capabilities: capabilities).any }
        }
    }

    var selectionSubsets: GitChangesDisplay.SelectionSubsets {
        GitChangesDisplay.selectionSubsets(sections, selection: selection, capabilities: capabilities)
    }

    /// Neither 404 can change, so polling stops here.
    var isFinished: Bool {
        switch state {
        case .agentOutdated, .sessionGone: return true
        default: return false
        }
    }

    /// The visible-only poll: an immediate load, then one per interval until
    /// cancelled or the feed ends.
    func run() async {
        while !Task.isCancelled {
            await load()
            if isFinished { return }
            do {
                try await sleeper.sleep(for: Self.pollInterval)
            } catch {
                return
            }
        }
    }

    func load() async {
        // Only the newest request applies its result (appear, poll, push and
        // pull-to-refresh all call this), so a slow earlier fetch cannot
        // overwrite fresher content.
        loadGeneration += 1
        let generation = loadGeneration
        isLoading = true
        defer { if generation == loadGeneration { isLoading = false } }
        // A retry with nothing to show must visibly restart, or an identical
        // failure would look like the tap was ignored.
        if cached == nil, case .error = state {
            state = .loading
        }
        do {
            let fresh = try await agentAPI.getSessionGitStatus(sessionId: sessionId, etag: cached?.etag)
            guard generation == loadGeneration else { return }
            let body: GitStatusResponse
            if !fresh.unchanged {
                body = fresh
            } else if let cached {
                body = cached
            } else {
                body = try await agentAPI.getSessionGitStatus(sessionId: sessionId, etag: nil)
                guard generation == loadGeneration else { return }
            }
            cached = body
            staleError = nil
            state = Self.state(for: body)
            // Ticked rows that vanished with the refresh are dropped.
            let live = Set(sections.flatMap(\.rows).map(\.id))
            selection.formIntersection(live)
        } catch {
            guard generation == loadGeneration, !SessionGitError.isCancellation(error) else { return }
            apply(SessionGitError.map(error))
        }
    }

    /// Re-fetch when the agent reports a git change for this session's
    /// repository — by project name, or by the resolved `toplevel` /
    /// `common_dir` for a session without one. Before the first body nothing
    /// is known, so every push refreshes.
    func observeGitChanged() async {
        for await event in agentEventBus.events() {
            guard case .gitChanged(let name, let toplevel, let commonDir) = event,
                  matches(projectName: name, toplevel: toplevel, commonDir: commonDir)
            else { continue }
            await load()
        }
    }

    func diffRoute(for entry: GitStatusEntry) -> Route {
        .sessionGitDiff(
            sessionId: sessionId,
            path: entry.path,
            scope: GitChangesDisplay.diffScope(for: entry),
            origPath: entry.orig_path
        )
    }

    /// One `GET /api/version` per screen visit, from the screen's own task so
    /// the poll's request sequence stays exactly the reads. An unreadable probe
    /// keeps the read-only viewer.
    func loadCapabilities() async {
        guard let info = try? await agentAPI.getAgentVersion() else { return }
        capabilities = info.gitCapabilities
    }

    /// The terminal menu's Commit…, once.
    func consumeOpenCommit() {
        guard openCommitOnAppear else { return }
        openCommitOnAppear = false
        openCommit()
    }

    /// The terminal menu's Fetch / Pull / Push…, once.
    func consumeOpenSync() {
        guard openSyncOnAppear else { return }
        openSyncOnAppear = false
        openSync()
    }

    /// The terminal menu's Rebase…, once.
    func consumeOpenRebase() {
        guard openRebaseOnAppear else { return }
        openRebaseOnAppear = false
        openRebase()
    }

    /// A project-backed session picks from the project's branches; any other
    /// session — or a list that fails to load — types the ref.
    func openRebase() {
        rebaseSheet = RebaseSheetState()
        Task { await loadRebaseTargets() }
    }

    func loadRebaseTargets() async {
        guard rebaseSheet != nil else { return }
        guard let name = projectName() else {
            rebaseSheet?.targets = .typed
            return
        }
        do {
            let items = try await agentAPI.getGitItems(projectName: name).items
            rebaseSheet?.targets = .ready(items)
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            rebaseSheet?.targets = .typed
        }
    }

    func closeRebase() {
        guard rebaseSheet?.isStarting != true else { return }
        rebaseSheet = nil
    }

    func startRebase(onto: String) async {
        let target = onto.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !target.isEmpty, !isMutating, rebaseSheet != nil else { return }
        // The branch is read before the call: HEAD is detached while a rebase runs.
        let branch = GitChangesDisplay.rebasedBranch(repo)
        isMutating = true
        rebaseSheet?.isStarting = true
        rebaseSheet?.error = nil
        defer { isMutating = false }
        do {
            let result = try await agentAPI.rebaseSessionGit(sessionId: sessionId, onto: target)
            rebaseSheet = nil
            toast = GitChangesDisplay.rebaseOutcomeToast(result, branch: branch, onto: target)
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            let mapped = SessionGitError.map(error)
            rebaseSheet?.isStarting = false
            rebaseSheet?.error = CommitError(
                message: SessionGitCopy.rebaseMessage(for: mapped),
                output: SessionGitCopy.rebaseOutput(for: mapped)
            )
        }
        await load()
    }

    func continueRebase() async {
        await runRebaseStep("continue")
    }

    func requestRebaseStep(_ step: RebasePromptStep) {
        pendingRebaseStep = step
        rebasePrompt = GitChangesDisplay.rebaseStepPrompt(step, repo: repo)
    }

    /// The dialog went away without a choice: clears the prompt and the step
    /// together (the `isPresented` binding must not call this).
    func dismissRebaseStep() {
        rebasePrompt = nil
        pendingRebaseStep = nil
    }

    func confirmRebaseStep() async {
        guard let step = pendingRebaseStep else { return }
        pendingRebaseStep = nil
        rebasePrompt = nil
        await runRebaseStep(step == .skip ? "skip" : "abort")
    }

    private func runRebaseStep(_ step: String) async {
        guard !isMutating else { return }
        let branch = GitChangesDisplay.rebasedBranch(repo)
        isMutating = true
        defer { isMutating = false }
        do {
            let result = try await agentAPI.stepSessionGitRebase(sessionId: sessionId, step: step)
            toast = GitChangesDisplay.rebaseOutcomeToast(result, branch: branch, onto: nil)
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            toast = ToastMessage(text: SessionGitCopy.rebaseMessage(for: SessionGitError.map(error)), tone: .danger)
        }
        await load()
    }

    /// Stage a fixed conflict; the agent refuses a file that still holds markers.
    func resolve(_ row: GitChangesRow) async {
        await stage([row])
    }

    /// Every job the agent reports for this session, as the socket frames
    /// arrive: how the screen adopts a job another client started, and how
    /// the starter sees its own job before the HTTP answer lands (the first
    /// frame goes out before the response).
    func observeGitJobFrames() async {
        for await event in agentEventBus.events() {
            let frame: GitJobResponse
            switch event {
            case .gitJobProgress(let job), .gitJobDone(let job):
                frame = job
            default:
                continue
            }
            guard frame.session_id == sessionId, frame.job_id != dismissedJobId else { continue }
            if job?.jobId == frame.job_id {
                apply(frame)
            } else {
                job = GitJobBanner(jobId: frame.job_id, job: frame)
                scheduleAutoDismissIfDone(frame)
            }
        }
    }

    /// The reconcile poll: a snapshot at once, then one per interval while the
    /// job runs and the app is not in the background. Restarted by the
    /// screen's `.task(id:)` whenever the job changes.
    func runJobReconcile() async {
        while !Task.isCancelled {
            guard let banner = job, banner.running else { return }
            await pollJob(banner.jobId)
            guard job?.running == true else { return }
            do {
                try await sleeper.sleep(for: Self.jobPollInterval)
            } catch {
                return
            }
        }
    }

    func openSync() {
        syncSheet = SyncSheetState()
    }

    func closeSync() {
        guard syncSheet?.isStarting != true else { return }
        syncSheet = nil
    }

    /// The sheet's plain rows: fetch, a fast-forward pull, a push without force.
    func startSync(op: String) async {
        await launchSync(op: op, pullMode: GitPullRequest.modeFFOnly, force: false)
    }

    func requestFollowUp(_ followUp: SyncFollowUp) {
        pendingFollowUp = followUp
        syncPrompt = GitChangesDisplay.followUpPrompt(followUp, repo: repo)
    }

    /// The dialog went away without a choice: clears the prompt and the
    /// follow-up together (the `isPresented` binding must not call this).
    func dismissFollowUp() {
        syncPrompt = nil
        pendingFollowUp = nil
    }

    func confirmFollowUp() async {
        guard let followUp = pendingFollowUp else { return }
        pendingFollowUp = nil
        syncPrompt = nil
        switch followUp {
        case .pullRebase:
            await launchSync(op: GitJobResponse.opPull, pullMode: GitPullRequest.modeRebase, force: false)
        case .pullMerge:
            await launchSync(op: GitJobResponse.opPull, pullMode: GitPullRequest.modeMerge, force: false)
        case .forcePush:
            await launchSync(op: GitJobResponse.opPush, pullMode: GitPullRequest.modeFFOnly, force: true)
        }
    }

    func cancelSync() async {
        guard let banner = job, banner.running else { return }
        do {
            let fresh = try await agentAPI.cancelSessionGitJob(sessionId: sessionId, jobId: banner.jobId)
            apply(fresh)
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            toast = ToastMessage(text: SessionGitCopy.mutationMessage(for: SessionGitError.map(error)), tone: .danger)
        }
    }

    func dismissJobResult() {
        dismissedJobId = job?.jobId
        job = nil
    }

    private func launchSync(op: String, pullMode: String, force: Bool) async {
        guard syncSheet?.isStarting != true else { return }
        syncSheet?.isStarting = true
        syncSheet?.error = nil
        defer { syncSheet?.isStarting = false }
        do {
            let started = try await agentAPI.startSessionGitJob(
                sessionId: sessionId, op: op, pullMode: pullMode, force: force
            )
            dismissedJobId = nil
            job = GitJobBanner(jobId: started.job_id, job: started)
            scheduleAutoDismissIfDone(started)
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            let mapped = SessionGitError.map(error)
            // Another client got there first: follow its job instead of failing.
            if case .git(let code, let id?)? = mapped as? SessionGitError,
               code == SessionGitError.codeJobRunning, !id.isEmpty {
                dismissedJobId = nil
                job = GitJobBanner(jobId: id, job: nil)
            }
            let copy = SessionGitCopy.mutationMessage(for: mapped)
            if syncSheet != nil {
                syncSheet?.error = copy
            } else {
                toast = ToastMessage(text: copy, tone: .danger)
            }
        }
    }

    private func pollJob(_ jobId: String) async {
        do {
            let fresh = try await agentAPI.getSessionGitJob(sessionId: sessionId, jobId: jobId)
            apply(fresh)
        } catch {
            guard !SessionGitError.isCancellation(error), job?.jobId == jobId else { return }
            // A forgotten job, a gone session or an outdated agent: nothing
            // left to poll (Android's feed and the web query stop the same way).
            switch SessionGitError.map(error) as? SessionGitError {
            case .git(let code, _)? where code == SessionGitError.codeJobNotFound:
                job?.lost = true
            case .agentOutdated?, .sessionGone?:
                job?.lost = true
            default:
                break
            }
        }
    }

    /// Monotonic: a `running` snapshot never overwrites a `done` one — a poll
    /// issued just before the terminal frame can answer after it.
    private func apply(_ fresh: GitJobResponse) {
        guard var banner = job, banner.jobId == fresh.job_id else { return }
        if let current = banner.job, !current.running, fresh.running { return }
        banner.job = fresh
        banner.lost = false
        job = banner
        scheduleAutoDismissIfDone(fresh)
    }

    /// A successful job clears its own banner; a failure stays until dismissed,
    /// because its follow-ups live behind it.
    private func scheduleAutoDismissIfDone(_ snapshot: GitJobResponse) {
        guard !snapshot.running, snapshot.ok == true else { return }
        let jobId = snapshot.job_id
        Task { [weak self] in
            guard let self else { return }
            try? await sleeper.sleep(for: Self.jobResultDismissDelay)
            if job?.jobId == jobId { dismissJobResult() }
        }
    }

    func rows(for ids: Set<String>) -> [GitChangesRow] {
        sections.flatMap(\.rows).filter { ids.contains($0.id) }
    }

    func stage(_ rows: [GitChangesRow]) async {
        await mutate(rows) { [self] in
            try await agentAPI.stageSessionGit(sessionId: sessionId, paths: GitChangesDisplay.paths(for: rows))
        }
    }

    func unstage(_ rows: [GitChangesRow]) async {
        await mutate(rows) { [self] in
            try await agentAPI.unstageSessionGit(sessionId: sessionId, paths: GitChangesDisplay.paths(for: rows))
        }
    }

    func requestDiscard(_ rows: [GitChangesRow]) {
        guard !rows.isEmpty else { return }
        pendingDiscard = rows
        discardPrompt = GitChangesDisplay.discardPrompt(for: rows)
    }

    /// The dialog went away without a choice (Cancel, or a tap outside): the
    /// `isPresented` binding must not call this — SwiftUI writes it false
    /// before the confirm action runs, which would empty the rows underneath
    /// `confirmDiscard`. It clears `discardPrompt` alone.
    func dismissDiscard() {
        discardPrompt = nil
        pendingDiscard = []
    }

    /// One request per scope: a selection can mix modified and untracked
    /// files, and the route takes one scope at a time.
    func confirmDiscard() async {
        let rows = pendingDiscard
        pendingDiscard = []
        discardPrompt = nil
        await mutate(rows) { [self] in
            let byScope = Dictionary(grouping: rows) { GitChangesDisplay.discardScope(for: $0.entry) }
            for (scope, scoped) in byScope.sorted(by: { $0.key < $1.key }) {
                try await agentAPI.discardSessionGit(
                    sessionId: sessionId,
                    paths: GitChangesDisplay.paths(for: scoped),
                    scope: scope
                )
            }
        }
    }

    func openCommit() {
        commitSheet = CommitSheetState()
        Task { await loadCommitContext() }
    }

    func closeCommit() {
        guard commitSheet?.isCommitting != true else { return }
        commitSheet = nil
    }

    func loadCommitContext() async {
        guard commitSheet != nil else { return }
        do {
            let response = try await agentAPI.getSessionGitCommitContext(sessionId: sessionId)
            commitSheet?.context = .ready(author: response.author, head: response.head)
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            commitSheet?.context = .failed(SessionGitCopy.mutationMessage(for: SessionGitError.map(error)))
        }
    }

    func commit(summary: String, description: String, amend: Bool, noVerify: Bool) async {
        guard !isMutating, commitSheet != nil else { return }
        let message = GitChangesDisplay.composeCommitMessage(summary: summary, description: description)
        guard !message.isEmpty else { return }
        isMutating = true
        commitSheet?.isCommitting = true
        commitSheet?.error = nil
        defer { isMutating = false }
        do {
            let result = try await agentAPI.commitSessionGit(
                sessionId: sessionId,
                request: GitCommitRequest(message: message, amend: amend, no_verify: noVerify)
            )
            commitSheet = nil
            toast = ToastMessage(text: GitChangesDisplay.commitNotice(result), tone: .ok)
            await load()
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            let mapped = SessionGitError.map(error)
            var output: String?
            if case .git(let code, let detail)? = mapped as? SessionGitError, code == SessionGitError.codeHookFailed {
                output = detail
            }
            commitSheet?.isCommitting = false
            commitSheet?.error = CommitError(message: SessionGitCopy.mutationMessage(for: mapped), output: output)
        }
    }

    /// One mutation at a time; on success the ticked rows are forgotten and
    /// the list re-reads the working tree at once — the agent's `git.changed`
    /// echo would do the same a moment later through the etag path.
    private func mutate(_ rows: [GitChangesRow], _ body: () async throws -> Void) async {
        guard !rows.isEmpty, !isMutating else { return }
        isMutating = true
        defer { isMutating = false }
        do {
            try await body()
            selection.subtract(rows.map(\.id))
        } catch {
            guard !SessionGitError.isCancellation(error) else { return }
            toast = ToastMessage(text: SessionGitCopy.mutationMessage(for: SessionGitError.map(error)), tone: .danger)
        }
        await load()
    }

    private func matches(projectName eventProject: String, toplevel: String?, commonDir: String?) -> Bool {
        guard let repo = cached?.repo else { return true }
        if let own = projectName(), own == eventProject { return true }
        if let toplevel, toplevel == repo.toplevel { return true }
        return commonDir != nil && commonDir == repo.common_dir
    }

    private func apply(_ error: any Error) {
        switch error as? SessionGitError {
        case .agentOutdated?:
            state = .agentOutdated
        case .sessionGone?:
            state = .sessionGone
        default:
            let message = SessionGitCopy.errorMessage(for: error)
            if cached != nil {
                staleError = message
            } else {
                state = .error(message)
            }
        }
    }

    private static func state(for body: GitStatusResponse) -> State {
        guard let repo = body.repo, body.unavailable_reason == nil else {
            return .unavailable(
                reason: body.unavailable_reason ?? GitStatusResponse.unavailableNotARepo,
                cwd: body.cwd
            )
        }
        return .ready(status: body, repo: repo, sections: GitChangesDisplay.sections(for: body))
    }
}

/// Pure derivations for the Changes screen (Android `GitChangesUiState.kt`).
enum GitChangesDisplay {
    struct Badge: Equatable {
        let label: String
        let tone: PixelTone
    }

    static let sectionOrder: [(group: String, title: String)] = [
        (GitStatusEntry.groupConflicts, "Conflicts"),
        (GitStatusEntry.groupStaged, "Staged"),
        (GitStatusEntry.groupUnstaged, "Changes"),
        (GitStatusEntry.groupUntracked, "Untracked"),
    ]

    /// The known groups in their fixed order, then any group the agent adds
    /// later, sorted, so a new value renders instead of vanishing.
    static func sections(for status: GitStatusResponse) -> [GitChangesSection] {
        let byGroup = Dictionary(grouping: status.entries, by: \.group)
        let known = sectionOrder.compactMap { order in
            byGroup[order.group].map { section(order.group, title: order.title, entries: $0) }
        }
        let other = byGroup.keys
            .filter { group in !sectionOrder.contains { $0.group == group } }
            .sorted()
            .compactMap { group in
                byGroup[group].map { section(group, title: group.replacingOccurrences(of: "_", with: " "), entries: $0) }
            }
        return known + other
    }

    private static func section(_ group: String, title: String, entries: [GitStatusEntry]) -> GitChangesSection {
        var seen: [String: Int] = [:]
        let rows = entries.map { entry -> GitChangesRow in
            let occurrence = (seen[entry.path] ?? 0) + 1
            seen[entry.path] = occurrence
            return GitChangesRow(id: "\(group):\(occurrence):\(entry.path)", entry: entry)
        }
        return GitChangesSection(group: group, title: title, rows: rows)
    }

    /// Conflicted files are diffed against HEAD, which the agent serves as
    /// `unstaged`.
    static func diffScope(for entry: GitStatusEntry) -> String {
        switch entry.group {
        case GitStatusEntry.groupStaged: return GitDiffResponse.scopeStaged
        case GitStatusEntry.groupUntracked: return GitDiffResponse.scopeUntracked
        default: return GitDiffResponse.scopeUnstaged
        }
    }

    static func badge(for entry: GitStatusEntry) -> Badge {
        switch entry.group {
        case GitStatusEntry.groupConflicts:
            return Badge(label: entry.code.isEmpty ? "U" : entry.code, tone: .danger)
        case GitStatusEntry.groupUntracked:
            return Badge(label: "?", tone: .muted)
        default:
            switch entry.code {
            case "A": return Badge(label: "A", tone: .ok)
            case "D": return Badge(label: "D", tone: .danger)
            case "R", "C": return Badge(label: entry.code, tone: .accent)
            default: return Badge(label: entry.code.isEmpty ? "M" : entry.code, tone: .warning)
            }
        }
    }

    static func headLabel(_ repo: GitRepoInfo) -> String {
        let head = repo.head
        if head.initial { return "No commits yet" }
        if head.detached { return "detached @ \(head.short_oid ?? "HEAD")" }
        return head.branch ?? head.short_oid ?? "HEAD"
    }

    static func upstreamLabel(_ upstream: GitUpstream?) -> String? {
        guard let upstream else { return nil }
        guard let ahead = upstream.ahead, let behind = upstream.behind else {
            return "\(upstream.ref) · upstream gone"
        }
        return "↑\(ahead) ↓\(behind) · \(upstream.ref)"
    }

    static func stateLabel(_ state: String) -> String? {
        switch state {
        case GitRepoInfo.stateClean: return nil
        case GitRepoInfo.stateCherryPicking: return "cherry-picking"
        default: return state.replacingOccurrences(of: "_", with: " ")
        }
    }

    static func secondaryLabel(_ entry: GitStatusEntry) -> String? {
        if let orig = entry.orig_path { return "\(orig) → \(entry.path)" }
        return entry.directory
    }

    /// Counts come from the full parse, but when the agent's byte cap hit
    /// before the entry cap they are a lower bound too — so the shown figure
    /// is what actually arrived and the total is never claimed exact.
    static func truncatedNotice(_ status: GitStatusResponse) -> String {
        let shown = status.entries.count
        if let total = status.counts?.total, total > shown {
            return "Showing the first \(shown) of at least \(total) entries."
        }
        return "List cut short after \(shown) entries."
    }

    /// What a row can do: a staged entry is unstaged, a modified or untracked one is staged or
    /// discarded, a conflict is marked resolved once fixed — only on an agent that refuses to
    /// stage leftover markers (`resolve`). Each gated on its capability, so an older agent
    /// shows no control that would 404.
    struct RowActions: Equatable {
        var stage = false
        var unstage = false
        var discard = false
        /// Stage a conflicted file once it is fixed; one file at a time, never from the selection.
        var resolve = false

        /// Whether the selection has anything for this row; `resolve` is per row only.
        var any: Bool { stage || unstage || discard }
        static let none = RowActions()
    }

    static func rowActions(_ entry: GitStatusEntry, capabilities: GitCapabilities) -> RowActions {
        switch entry.group {
        case GitStatusEntry.groupStaged:
            return RowActions(unstage: capabilities.stage)
        case GitStatusEntry.groupUnstaged, GitStatusEntry.groupUntracked:
            return RowActions(stage: capabilities.stage, discard: capabilities.discard)
        case GitStatusEntry.groupConflicts:
            return RowActions(resolve: capabilities.stage && capabilities.resolve)
        default:
            return .none
        }
    }

    /// The discard route's scope for an entry; conflicts never reach it.
    static func discardScope(for entry: GitStatusEntry) -> String {
        entry.group == GitStatusEntry.groupUntracked
            ? GitDiscardRequest.scopeUntracked
            : GitDiscardRequest.scopeUnstaged
    }

    /// The paths a request names for these rows: a rename needs both its
    /// sides (`git reset` on the new name alone leaves the deletion staged).
    static func paths(for rows: [GitChangesRow]) -> [String] {
        var seen: Set<String> = []
        var out: [String] = []
        for row in rows {
            for path in [row.entry.path, row.entry.orig_path].compactMap({ $0 }) where seen.insert(path).inserted {
                out.append(path)
            }
        }
        return out
    }

    /// The selected rows each toolbar action applies to; a button is enabled
    /// when its list is non-empty.
    struct SelectionSubsets: Equatable {
        var stage: [GitChangesRow] = []
        var unstage: [GitChangesRow] = []
        var discard: [GitChangesRow] = []
    }

    static func selectionSubsets(
        _ sections: [GitChangesSection],
        selection: Set<String>,
        capabilities: GitCapabilities
    ) -> SelectionSubsets {
        var subsets = SelectionSubsets()
        for row in sections.flatMap(\.rows) where selection.contains(row.id) {
            let actions = rowActions(row.entry, capabilities: capabilities)
            if actions.stage { subsets.stage.append(row) }
            if actions.unstage { subsets.unstage.append(row) }
            if actions.discard { subsets.discard.append(row) }
        }
        return subsets
    }

    /// A section header's whole-section action; nil for Conflicts and unknown groups.
    struct SectionAction: Equatable {
        let label: String
        let stage: Bool
    }

    static func sectionAction(_ section: GitChangesSection, capabilities: GitCapabilities) -> SectionAction? {
        guard capabilities.stage else { return nil }
        switch section.group {
        case GitStatusEntry.groupStaged:
            return SectionAction(label: "Unstage all", stage: false)
        case GitStatusEntry.groupUnstaged, GitStatusEntry.groupUntracked:
            return SectionAction(label: "Stage all", stage: true)
        default:
            return nil
        }
    }

    static func discardPrompt(for rows: [GitChangesRow]) -> DiscardPrompt {
        if rows.count == 1, let only = rows.first {
            let untracked = only.entry.group == GitStatusEntry.groupUntracked
            return DiscardPrompt(
                rows: rows,
                title: untracked ? "Delete untracked file?" : "Discard changes?",
                body: untracked
                    ? "\(only.entry.path) is not tracked by git and will be deleted. This cannot be undone."
                    : "Changes to \(only.entry.path) will be thrown away. This cannot be undone."
            )
        }
        return DiscardPrompt(
            rows: rows,
            title: "Discard \(rows.count) files?",
            body: "Their working-tree changes will be thrown away and untracked ones deleted. This cannot be undone."
        )
    }

    /// `subject\n\nbody` — the shape `git commit -F` reads.
    static func composeCommitMessage(summary: String, description: String) -> String {
        let subject = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        let body = description.trimmingCharacters(in: .whitespacesAndNewlines)
        return body.isEmpty ? subject : "\(subject)\n\n\(body)"
    }

    static func commitButtonLabel(stagedCount: Int, amend: Bool) -> String {
        if amend { return "Amend commit" }
        return stagedCount == 1 ? "Commit 1 file" : "Commit \(stagedCount) files"
    }

    static func commitNotice(_ result: GitCommitResponse) -> String {
        "Committed \(result.short_oid) · \(result.subject)"
    }

    static func authorLine(_ author: GitCommitAuthor) -> String {
        "Committing as \(author.name) <\(author.email)>"
    }

    static let jobLostCopy = "Lost track of the job — refresh to see the result."

    /// One of the Sync sheet's three actions and whether it can run right now.
    struct SyncRow: Equatable {
        let op: String
        let label: String
        let detail: String
        let enabled: Bool
    }

    /// The Sync sheet's rows from the same status the header renders; the copy
    /// is shared with Android `syncRows` and web `syncRows`. A row exists only
    /// when the agent advertises its capability; Fetch is always possible,
    /// Pull needs something to pull, Push something to push — or a branch with
    /// no upstream yet, which it publishes.
    static func syncRows(_ repo: GitRepoInfo?, capabilities: GitCapabilities) -> [SyncRow] {
        var rows: [SyncRow] = []
        let head = repo?.head
        let upstream = repo?.upstream
        let ref = upstream?.ref
        let blocked: String?
        if let head {
            blocked = head.initial ? "No commits yet" : head.detached ? "Detached HEAD" : nil
        } else {
            blocked = "Repository unavailable"
        }
        if capabilities.fetch {
            rows.append(SyncRow(
                op: GitJobResponse.opFetch, label: "Fetch", detail: "Update remote-tracking branches", enabled: true
            ))
        }
        if capabilities.pull {
            if let blocked {
                rows.append(SyncRow(op: GitJobResponse.opPull, label: "Pull", detail: blocked, enabled: false))
            } else if let upstream {
                if let behind = upstream.behind {
                    if behind == 0 {
                        rows.append(SyncRow(op: GitJobResponse.opPull, label: "Pull", detail: "Nothing to pull", enabled: false))
                    } else {
                        rows.append(SyncRow(
                            op: GitJobResponse.opPull,
                            label: "Pull",
                            detail: "Fast-forward \(behind) \(behind == 1 ? "commit" : "commits") from \(ref ?? "upstream")",
                            enabled: true
                        ))
                    }
                } else {
                    rows.append(SyncRow(op: GitJobResponse.opPull, label: "Pull", detail: "Upstream gone", enabled: false))
                }
            } else {
                rows.append(SyncRow(op: GitJobResponse.opPull, label: "Pull", detail: "No upstream", enabled: false))
            }
        }
        if capabilities.push {
            if let blocked {
                rows.append(SyncRow(op: GitJobResponse.opPush, label: "Push", detail: blocked, enabled: false))
            } else if let upstream {
                if let ahead = upstream.ahead {
                    if ahead == 0 {
                        rows.append(SyncRow(op: GitJobResponse.opPush, label: "Push", detail: "Nothing to push", enabled: false))
                    } else {
                        rows.append(SyncRow(
                            op: GitJobResponse.opPush,
                            label: "Push",
                            detail: "\(ahead) \(ahead == 1 ? "commit" : "commits") to \(ref ?? "upstream")",
                            enabled: true
                        ))
                    }
                } else {
                    rows.append(SyncRow(
                        op: GitJobResponse.opPush, label: "Push", detail: "Recreate \(ref ?? "the upstream")", enabled: true
                    ))
                }
            } else {
                rows.append(SyncRow(
                    op: GitJobResponse.opPush, label: "Publish branch", detail: "Push and set the upstream", enabled: true
                ))
            }
        }
        return rows
    }

    static func jobTitle(_ op: String) -> String {
        switch op {
        case GitJobResponse.opFetch: return "Fetching…"
        case GitJobResponse.opPull: return "Pulling…"
        default: return "Pushing…"
        }
    }

    static func jobDoneTitle(_ op: String, ok: Bool) -> String {
        switch op {
        case GitJobResponse.opFetch: return ok ? "Fetched" : "Fetch failed"
        case GitJobResponse.opPull: return ok ? "Pulled" : "Pull failed"
        default: return ok ? "Pushed" : "Push failed"
        }
    }

    /// One line per job `error_code`, identical on the three clients. `message`
    /// is git's own explanation and is rendered beside it in monospace, so only
    /// an unknown code falls back to it here.
    static func jobErrorCopy(_ code: String?, message: String?) -> String {
        switch code ?? "" {
        case GitJobResponse.codeCancelled: return "Cancelled."
        case SessionGitError.codeGitTimeout: return "git took too long to answer. Try again."
        case GitJobResponse.codeAuthRequired:
            return "No credentials for this remote. Configure a credential helper or SSH agent on the agent host."
        case GitJobResponse.codeNetwork: return "Couldn't reach the remote."
        case GitJobResponse.codeRemoteNotFound: return "The remote doesn't exist."
        case GitJobResponse.codeRejected: return "The remote rejected the push."
        case GitJobResponse.codeNotFastForward: return "Your branch is behind the remote."
        case GitJobResponse.codeConflicts: return "Stopped on conflicts — resolve them in the terminal."
        case GitJobResponse.codeDirtyWorktree: return "Commit or stash your changes first."
        case SessionGitError.codeUnmerged: return "Resolve the conflicts first."
        case GitJobResponse.codeOperationInProgress: return "A rebase or merge is already in progress."
        case GitJobResponse.codeNoUpstream: return "This branch has no upstream."
        case GitJobResponse.codeNoRemote: return "This repository has no remote."
        case GitJobResponse.codeDetachedHead: return "Detached HEAD — check out a branch first."
        case SessionGitError.codeNoHead: return "No commits yet."
        case SessionGitError.codeHookFailed: return "A pre-push hook rejected the push."
        case SessionGitError.codeIndexLocked: return "Another git process is running — try again."
        default:
            if let message, !message.isEmpty { return message }
            return "git failed."
        }
    }

    /// The banner's one line: the title plus git's summary, or the title plus
    /// the failure copy.
    static func jobNotice(_ job: GitJobResponse) -> String {
        if job.running { return jobTitle(job.op) }
        let ok = job.ok == true
        let title = jobDoneTitle(job.op, ok: ok)
        if ok {
            if let message = job.message, !message.isEmpty { return "\(title) · \(message)" }
            return title
        }
        return "\(title): \(jobErrorCopy(job.error_code, message: job.message))"
    }

    /// Whether the failure's `message` adds anything the copy did not already say.
    static func showsJobOutput(_ job: GitJobResponse) -> Bool {
        guard job.ok == false, let message = job.message, !message.isEmpty else { return false }
        return jobErrorCopy(job.error_code, message: job.message) != message
    }

    /// What a failed job can be retried as: a pull that could not fast-forward
    /// as a rebase or a merge, a rejected push as a push with lease. Each
    /// rewrites history somewhere, so every one is confirmed first. A refused
    /// publish installs no upstream and a pruned one cannot be pulled until
    /// the next fetch, so — like the Pull row — the pulls are dropped when the
    /// status says so, never when it is unavailable.
    static func jobFollowUps(_ job: GitJobResponse, repo: GitRepoInfo? = nil) -> [SyncFollowUp] {
        guard !job.running, job.ok == false else { return [] }
        let pulls: [SyncFollowUp]
        if let repo, repo.upstream?.behind == nil {
            pulls = []
        } else {
            pulls = [.pullRebase, .pullMerge]
        }
        // Behind the remote: integrate first, whichever way the user prefers;
        // a push may also mean a deliberate rewrite, so the lease push stays
        // offered.
        if job.error_code == GitJobResponse.codeNotFastForward {
            return job.op == GitJobResponse.opPush ? pulls + [.forcePush] : pulls
        }
        if job.op == GitJobResponse.opPush, job.error_code == GitJobResponse.codeRejected {
            return [.forcePush]
        }
        return []
    }

    static func followUpLabel(_ followUp: SyncFollowUp) -> String {
        switch followUp {
        case .pullRebase: return "Pull with rebase…"
        case .pullMerge: return "Pull with merge…"
        case .forcePush: return "Force push (with lease)…"
        }
    }

    static func followUpPrompt(_ followUp: SyncFollowUp, repo: GitRepoInfo?) -> SyncPrompt {
        let ref = repo?.upstream?.ref ?? "the upstream"
        let branch = repo?.head.branch ?? "your branch"
        switch followUp {
        case .pullRebase:
            return SyncPrompt(
                followUp: followUp,
                title: "Pull with rebase?",
                body: "Replays your commits on top of \(ref).",
                confirm: "Rebase"
            )
        case .pullMerge:
            return SyncPrompt(
                followUp: followUp,
                title: "Pull with merge?",
                body: "Merges \(ref) into \(branch).",
                confirm: "Merge"
            )
        case .forcePush:
            return SyncPrompt(
                followUp: followUp,
                title: "Force push?",
                body: "Overwrites \(ref) with your branch. Proceeds only if the remote hasn't moved since your last fetch.",
                confirm: "Force push"
            )
        }
    }
}

extension GitChangesDisplay {
    struct RebaseTarget: Equatable, Identifiable {
        let value: String
        let label: String

        var id: String { value }
    }

    /// The rebase sheet's picker: local branches, then remote-tracking ones,
    /// from the project Git screen's rows. The session's own branch is left
    /// out by name — the rows' `is_current` is the project root's checkout,
    /// which for a session in a linked worktree is usually the very branch it
    /// wants to rebase onto.
    static func rebaseTargets(_ items: [GitItem], currentBranch: String?) -> [RebaseTarget] {
        func usable(_ item: GitItem) -> String? {
            guard let branch = item.branch, branch != currentBranch else { return nil }
            return branch
        }
        let local = items
            .filter { $0.kind == GitItem.kindBranchOnly || $0.kind == GitItem.kindBranchWithWorktree }
            .compactMap(usable)
            .map { RebaseTarget(value: $0, label: $0) }
        let remote = items
            .filter { $0.kind == GitItem.kindRemoteBranch }
            .compactMap(usable)
            .map { RebaseTarget(value: $0, label: "\($0) (remote)") }
        return local + remote
    }

    /// Why a rebase cannot start from this status, or nil. Untracked files do
    /// not block: git refuses only the ones a pick would overwrite.
    static func rebaseBlocker(_ status: GitStatusResponse?) -> String? {
        guard let repo = status?.repo else { return "Repository unavailable." }
        if repo.state != GitRepoInfo.stateClean { return "A rebase or merge is already in progress." }
        if repo.head.initial { return "This branch has no commits yet." }
        if let counts = status?.counts, counts.conflicts + counts.staged + counts.unstaged > 0 {
            return "Commit or discard your changes first."
        }
        return nil
    }

    /// The branch a rebase acts on, for copy: the one being rebased, else HEAD's.
    static func rebasedBranch(_ repo: GitRepoInfo?) -> String {
        repo?.rebase?.branch ?? repo?.head.branch ?? "the branch"
    }

    struct RebaseBanner: Equatable {
        let title: String
        /// Where it stopped and how far along, when the agent could read it.
        let progress: String?
        let hint: String
        /// False when `repo.rebase` is nil: a `git am` session, which reports
        /// `rebasing` too and refuses Continue / Skip / Abort, or an agent that
        /// predates the rebase routes.
        let actionable: Bool
    }

    /// The Changes screen's banner while the repository is rebasing.
    static func rebaseBanner(_ repo: GitRepoInfo?, conflicts: Int) -> RebaseBanner? {
        guard let repo, repo.state == GitRepoInfo.stateRebasing else { return nil }
        let info = repo.rebase
        let branch = info?.branch
        let onto = info?.onto_ref ?? info?.onto_short_oid
        let title: String
        switch (branch, onto) {
        case let (branch?, onto?): title = "Rebasing \(branch) onto \(onto)"
        case let (branch?, nil): title = "Rebasing \(branch)"
        case let (nil, onto?): title = "Rebasing onto \(onto)"
        case (nil, nil): title = "Rebase in progress"
        }
        var parts: [String] = []
        if let stopped = info?.stopped_on { parts.append("Stopped at \(stopped.short_oid) \(stopped.subject)") }
        if let step = info?.step, let total = info?.total { parts.append("\(step)/\(total)") }
        let actionable = info != nil
        let hint: String
        if !actionable {
            hint = "Continue, skip or abort it in the terminal."
        } else if conflicts > 0 {
            hint = "\(conflicts) \(conflicts == 1 ? "conflict" : "conflicts") — fix the files, then Mark resolved."
        } else {
            hint = "No conflicts left — Continue when ready."
        }
        return RebaseBanner(
            title: title,
            progress: parts.isEmpty ? nil : parts.joined(separator: " · "),
            hint: hint,
            actionable: actionable
        )
    }

    static func rebaseStepPrompt(_ step: RebasePromptStep, repo: GitRepoInfo?) -> RebasePrompt {
        let branch = rebasedBranch(repo)
        switch step {
        case .skip:
            let commit = repo?.rebase?.stopped_on.map { "\($0.short_oid) “\($0.subject)”" }
                ?? "The commit the rebase stopped at"
            return RebasePrompt(
                step: step,
                title: "Skip this commit?",
                body: "\(commit) will be left out of \(branch).",
                confirm: "Skip commit"
            )
        case .abort:
            return RebasePrompt(
                step: step,
                title: "Abort the rebase?",
                body: "\(branch) goes back to where it was before the rebase. "
                    + "Conflict resolutions made so far are discarded.",
                confirm: "Abort rebase"
            )
        }
    }

    /// The message a finished rebase call leaves. `onto` is what the user
    /// picked, known only for a start; Continue and Skip finish without it.
    static func rebaseOutcomeNotice(_ result: GitRebaseResponse, branch: String, onto: String?) -> String {
        switch result.outcome {
        case GitRebaseResponse.outcomeRebased:
            return onto.map { "Rebased \(branch) onto \($0)." } ?? "Rebase finished."
        case GitRebaseResponse.outcomeUpToDate:
            return "\(branch) is already up to date with \(onto ?? "that branch")."
        case GitRebaseResponse.outcomeAborted:
            return "Rebase aborted — \(branch) is back where it started."
        case GitRebaseResponse.outcomeStopped:
            if let message = result.message, !message.isEmpty { return "Rebase stopped: \(message)" }
            if let subject = result.rebase?.stopped_on?.subject {
                return "Rebase stopped at “\(subject)” — resolve the conflicts, then Continue."
            }
            return "Rebase stopped — resolve the conflicts, then Continue."
        default:
            return "Rebase: \(result.outcome)"
        }
    }

    static func rebaseOutcomeToast(_ result: GitRebaseResponse, branch: String, onto: String?) -> ToastMessage {
        ToastMessage(
            text: rebaseOutcomeNotice(result, branch: branch, onto: onto),
            tone: result.outcome == GitRebaseResponse.outcomeStopped ? .danger : .ok
        )
    }
}
