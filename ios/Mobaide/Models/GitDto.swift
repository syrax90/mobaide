import Foundation

/// One row of the project Git screen. `remote_branch` rows (`branch` is
/// `<remote>/<name>`, `remote` names the remote) arrive only when asked for
/// with `?remote=true`; `subject` / `committed_at` describe the tip commit.
struct GitItem: Codable, Equatable, Sendable {
    static let kindBranchWithWorktree = "branch_with_worktree"
    static let kindBranchOnly = "branch_only"
    static let kindOrphanWorktree = "orphan_worktree"
    static let kindRemoteBranch = "remote_branch"

    let kind: String
    let branch: String?
    let worktree_path: String?
    let is_current: Bool
    let upstream: String?
    let ahead: Int
    let behind: Int
    let gone: Bool
    let locked: Bool
    let locked_reason: String?
    let detached: Bool
    let prunable: Bool
    let remote: String?
    let subject: String?
    let committed_at: String?

    init(
        kind: String,
        branch: String? = nil,
        worktree_path: String? = nil,
        is_current: Bool = false,
        upstream: String? = nil,
        ahead: Int = 0,
        behind: Int = 0,
        gone: Bool = false,
        locked: Bool = false,
        locked_reason: String? = nil,
        detached: Bool = false,
        prunable: Bool = false,
        remote: String? = nil,
        subject: String? = nil,
        committed_at: String? = nil
    ) {
        self.kind = kind
        self.branch = branch
        self.worktree_path = worktree_path
        self.is_current = is_current
        self.upstream = upstream
        self.ahead = ahead
        self.behind = behind
        self.gone = gone
        self.locked = locked
        self.locked_reason = locked_reason
        self.detached = detached
        self.prunable = prunable
        self.remote = remote
        self.subject = subject
        self.committed_at = committed_at
    }

    var id: String {
        switch kind {
        case Self.kindBranchWithWorktree, Self.kindBranchOnly:
            return "\(kind):\(branch ?? worktree_path ?? "")"
        case Self.kindOrphanWorktree:
            return "\(kind):\(worktree_path ?? "")"
        default:
            return "\(kind):\(branch ?? "")|\(worktree_path ?? "")"
        }
    }

    /// False for the checked-out branch (git refuses); a remote-tracking row
    /// only with `deleteRemote` (`capabilities.git.delete_remote`) — an older
    /// agent answers the whole batch with a 422.
    func isDeletable(deleteRemote: Bool) -> Bool {
        !is_current && (kind != Self.kindRemoteBranch || deleteRemote)
    }

    /// *Remove worktree* keeps the branch: a worktree-backed row other than the
    /// project root's checkout.
    var canRemoveWorktree: Bool {
        !is_current && kind == Self.kindBranchWithWorktree && !(worktree_path ?? "").isEmpty
    }

    /// Check out is offered for a branch nothing has checked out: a `branch_only`
    /// row or a remote-tracking one; never the current branch or a worktree-backed row.
    var canCheckOut: Bool {
        !is_current && (kind == Self.kindBranchOnly || kind == Self.kindRemoteBranch)
    }
}

extension GitItem {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        kind = try container.decode(String.self, forKey: .kind)
        branch = try container.decodeIfPresent(String.self, forKey: .branch)
        worktree_path = try container.decodeIfPresent(String.self, forKey: .worktree_path)
        is_current = try container.decodeIfPresent(Bool.self, forKey: .is_current) ?? false
        upstream = try container.decodeIfPresent(String.self, forKey: .upstream)
        ahead = try container.decodeIfPresent(Int.self, forKey: .ahead) ?? 0
        behind = try container.decodeIfPresent(Int.self, forKey: .behind) ?? 0
        gone = try container.decodeIfPresent(Bool.self, forKey: .gone) ?? false
        locked = try container.decodeIfPresent(Bool.self, forKey: .locked) ?? false
        locked_reason = try container.decodeIfPresent(String.self, forKey: .locked_reason)
        detached = try container.decodeIfPresent(Bool.self, forKey: .detached) ?? false
        prunable = try container.decodeIfPresent(Bool.self, forKey: .prunable) ?? false
        remote = try container.decodeIfPresent(String.self, forKey: .remote)
        subject = try container.decodeIfPresent(String.self, forKey: .subject)
        committed_at = try container.decodeIfPresent(String.self, forKey: .committed_at)
    }
}

struct GitItemsResponse: Codable, Equatable, Sendable {
    let items: [GitItem]
}

struct GitDeleteItem: Codable, Equatable, Sendable {
    let kind: String
    let branch: String?
    let worktree_path: String?
    let force: Bool

    init(kind: String, branch: String? = nil, worktree_path: String? = nil, force: Bool = false) {
        self.kind = kind
        self.branch = branch
        self.worktree_path = worktree_path
        self.force = force
    }
}

extension GitDeleteItem {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        kind = try container.decode(String.self, forKey: .kind)
        branch = try container.decodeIfPresent(String.self, forKey: .branch)
        worktree_path = try container.decodeIfPresent(String.self, forKey: .worktree_path)
        force = try container.decodeIfPresent(Bool.self, forKey: .force) ?? false
    }
}

struct GitDeleteRequest: Codable, Equatable, Sendable {
    let items: [GitDeleteItem]
}

struct GitDeleteResultItem: Codable, Equatable, Sendable {
    let kind: String
    let target: String
    let ok: Bool
    let error: String?
    let error_detail: String?
}

struct GitDeleteResponse: Codable, Equatable, Sendable {
    let results: [GitDeleteResultItem]
}

struct BusyHolder: Codable, Equatable, Sendable {
    let kind: String
    let session_id: String?
    let project_name: String?
    let cwd: String?
    let worktree_path: String?
}

struct BusyByResponse: Codable, Equatable, Sendable {
    let busy_by: [BusyHolder]
}

/// `POST /api/projects/{name}/git/branches`.
struct GitBranchCreateRequest: Codable, Equatable, Sendable {
    let name: String
    let start_point: String?
    let checkout: Bool

    init(name: String, start_point: String? = nil, checkout: Bool = false) {
        self.name = name
        self.start_point = start_point
        self.checkout = checkout
    }
}

extension GitBranchCreateRequest {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        start_point = try container.decodeIfPresent(String.self, forKey: .start_point)
        checkout = try container.decodeIfPresent(Bool.self, forKey: .checkout) ?? false
    }
}

/// `POST /api/projects/{name}/git/checkout`; `ref` is a row's `branch` verbatim.
struct GitCheckoutRequest: Codable, Equatable, Sendable {
    let ref: String
}

struct GitBranchResponse: Codable, Equatable, Sendable {
    let branch: String
    let checked_out: Bool
    let created: Bool
}

// MARK: - Session-scoped read-only Changes API
//
// Wire-key-verbatim mirrors of the agent's `GitStatusResponse` /
// `GitDiffResponse` pydantic models. The agent always emits every key
// (nulls included), so only the nullable fields are optionals here.

struct GitHead: Codable, Equatable, Sendable {
    let oid: String?
    let short_oid: String?
    let branch: String?
    let detached: Bool
    let initial: Bool
}

struct GitUpstream: Codable, Equatable, Sendable {
    let ref: String
    let ahead: Int?
    let behind: Int?
}

struct GitRebaseCommit: Codable, Equatable, Sendable {
    let oid: String
    let short_oid: String
    let subject: String
}

/// The rebase in progress: `branch` is nil for a detached HEAD, `onto_ref` a
/// branch pointing at the base or nil, `stopped_on` the commit git stopped at.
/// Every key can be nil — a rebase git was killed in need not have written
/// every state file.
struct GitRebaseInfo: Codable, Equatable, Sendable {
    var branch: String?
    var onto_oid: String?
    var onto_short_oid: String?
    var onto_ref: String?
    var stopped_on: GitRebaseCommit?
    var step: Int?
    var total: Int?
}

struct GitRepoInfo: Codable, Equatable, Sendable {
    let toplevel: String
    let git_dir: String
    let common_dir: String
    let is_linked_worktree: Bool
    let head: GitHead
    let upstream: GitUpstream?
    let state: String
    /// Set only while `state` is `rebasing`, by an agent with `capabilities.git.rebase`.
    var rebase: GitRebaseInfo? = nil
}

struct GitStatusEntry: Codable, Equatable, Sendable {
    let group: String
    let path: String
    let orig_path: String?
    let code: String
    let index_status: String
    let worktree_status: String
    let rename_score: Int?
    let submodule: Bool
}

struct GitStatusCounts: Codable, Equatable, Sendable {
    let conflicts: Int
    let staged: Int
    let unstaged: Int
    let untracked: Int
    let total: Int
}

struct GitStatusResponse: Codable, Equatable, Sendable {
    let session_id: String
    let cwd: String?
    let etag: String
    let unchanged: Bool
    let unavailable_reason: String?
    let repo: GitRepoInfo?
    let entries: [GitStatusEntry]
    let counts: GitStatusCounts?
    let truncated: Bool
    let entry_limit: Int
}

struct GitDiffLine: Codable, Equatable, Sendable {
    let t: String
    let old: Int?
    let new: Int?
    let text: String
    let no_newline: Bool
}

struct GitDiffHunk: Codable, Equatable, Sendable {
    let old_start: Int
    let old_lines: Int
    let new_start: Int
    let new_lines: Int
    let heading: String
    let lines: [GitDiffLine]
}

struct GitDiffResponse: Codable, Equatable, Sendable {
    let path: String
    let orig_path: String?
    let scope: String
    let kind: String
    let change: String?
    let old_mode: String?
    let new_mode: String?
    let additions: Int
    let deletions: Int
    let hunks: [GitDiffHunk]
    let truncated: Bool
    let total_lines: Int
    let line_limit: Int
    let byte_limit: Int
}

// Wire enums stay `String` so a future agent value never fails the decode;
// the constants are the values the screens branch on (Android `GitDto.kt`).

extension GitRepoInfo {
    static let stateClean = "clean"
    static let stateMerging = "merging"
    static let stateRebasing = "rebasing"
    static let stateCherryPicking = "cherry_picking"
    static let stateReverting = "reverting"
    static let stateBisecting = "bisecting"
}

extension GitStatusEntry {
    static let groupConflicts = "conflicts"
    static let groupStaged = "staged"
    static let groupUnstaged = "unstaged"
    static let groupUntracked = "untracked"

    var fileName: String { Self.fileName(of: path) }

    var directory: String? { Self.directory(of: path) }

    /// The last path segment; the whole path when it has no `/`. Git lists an
    /// untracked nested repository or worktree as `dir/`, so a trailing slash
    /// stays on the name instead of leaving it empty.
    static func fileName(of path: String) -> String {
        guard let slash = withoutTrailingSlash(path).lastIndex(of: "/") else { return path }
        return String(path[path.index(after: slash)...])
    }

    /// Everything before `fileName(of:)`, or nil for a top-level entry.
    static func directory(of path: String) -> String? {
        let trimmed = withoutTrailingSlash(path)
        guard let slash = trimmed.lastIndex(of: "/") else { return nil }
        let head = String(trimmed[..<slash])
        return head.isEmpty ? nil : head
    }

    private static func withoutTrailingSlash(_ path: String) -> Substring {
        path.hasSuffix("/") ? path.dropLast() : path[...]
    }
}

extension GitStatusResponse {
    static let unavailableNotARepo = "not_a_repo"
    static let unavailableBareRepository = "bare_repository"
    static let unavailableCwdMissing = "cwd_missing"
    static let unavailableUnsafeDirectory = "unsafe_directory"
    static let unavailableGitTooOld = "git_too_old"
}

extension GitDiffLine {
    static let typeContext = "ctx"
    static let typeAdd = "add"
    static let typeDelete = "del"
}

extension GitDiffResponse {
    static let scopeStaged = "staged"
    static let scopeUnstaged = "unstaged"
    static let scopeUntracked = "untracked"

    static let kindText = "text"
    static let kindBinary = "binary"
    static let kindSubmodule = "submodule"
    static let kindTooLarge = "too_large"
    static let kindMissing = "missing"

    static let changeModified = "modified"
    static let changeAdded = "added"
    static let changeDeleted = "deleted"
    static let changeRenamed = "renamed"
    static let changeCopied = "copied"
    static let changeTypeChanged = "type_changed"
}

/// Repo-relative paths as `status` reported them; a renamed entry lists both
/// `path` and `orig_path`.
struct GitPathsRequest: Codable, Equatable, Sendable {
    let paths: [String]
}

struct GitDiscardRequest: Codable, Equatable, Sendable {
    let paths: [String]
    let scope: String

    static let scopeUnstaged = "unstaged"
    static let scopeUntracked = "untracked"
}

struct GitMutationResponse: Codable, Equatable, Sendable {
    let ok: Bool
}

struct GitCommitRequest: Codable, Equatable, Sendable {
    let message: String
    let amend: Bool
    let no_verify: Bool
}

struct GitCommitResponse: Codable, Equatable, Sendable {
    let oid: String
    let short_oid: String
    let subject: String
}

struct GitCommitAuthor: Codable, Equatable, Sendable {
    let name: String
    let email: String
}

struct GitCommitHead: Codable, Equatable, Sendable {
    let oid: String
    let short_oid: String
    let subject: String
    let body: String
}

/// What the commit sheet needs before the user types: `author` is nil when git
/// cannot determine an identity (the commit would be refused with
/// `identity_required`), `head` is nil on an unborn branch (nothing to amend).
struct GitCommitContextResponse: Codable, Equatable, Sendable {
    let author: GitCommitAuthor?
    let head: GitCommitHead?
}

struct GitPullRequest: Codable, Equatable, Sendable {
    let mode: String

    static let modeFFOnly = "ff_only"
    static let modeRebase = "rebase"
    static let modeMerge = "merge"
}

struct GitPushRequest: Codable, Equatable, Sendable {
    let force: Bool
}

struct GitRebaseRequest: Codable, Equatable, Sendable {
    let onto: String
}

/// What a rebase route did. `outcome` is an open vocabulary; `rebase` is set
/// only when `stopped`, and `message` explains a stop that is not a conflict.
struct GitRebaseResponse: Codable, Equatable, Sendable {
    let outcome: String
    let rebase: GitRebaseInfo?
    let message: String?

    static let outcomeRebased = "rebased"
    static let outcomeUpToDate = "up_to_date"
    static let outcomeStopped = "stopped"
    static let outcomeAborted = "aborted"
}

/// One fetch / pull / push job: the body every job route answers and the
/// payload of `git.job.progress` / `git.job.done`. `phase` / `percent` /
/// `line` mirror git's own `--progress` output; `ok` / `error_code` /
/// `message` stay nil until `status` is `done`. `error_code` is an open
/// vocabulary — an unknown code renders from `message`.
struct GitJobResponse: Codable, Equatable, Sendable {
    let job_id: String
    let session_id: String
    let op: String
    let status: String
    let phase: String?
    let percent: Int?
    let line: String?
    let ok: Bool?
    let error_code: String?
    let message: String?

    var running: Bool { status != Self.statusDone }

    static let opFetch = "fetch"
    static let opPull = "pull"
    static let opPush = "push"
    static let statusRunning = "running"
    static let statusDone = "done"
    static let codeCancelled = "cancelled"
    static let codeAuthRequired = "auth_required"
    static let codeNetwork = "network"
    static let codeRemoteNotFound = "remote_not_found"
    static let codeRejected = "rejected"
    static let codeNotFastForward = "not_fast_forward"
    static let codeConflicts = "conflicts"
    static let codeDirtyWorktree = "dirty_worktree"
    static let codeOperationInProgress = "operation_in_progress"
    static let codeNoUpstream = "no_upstream"
    static let codeNoRemote = "no_remote"
    static let codeDetachedHead = "detached_head"
}
