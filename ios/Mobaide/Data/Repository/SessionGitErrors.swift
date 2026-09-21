import Foundation

/// Failures of the session-scoped git Changes routes, typed so the screens can
/// tell "update your agent" from "session ended" from "git itself refused"
/// (Android `SessionGitException`).
///
/// A coded `detail` object is a git answer whatever the status — the job
/// routes' `404 job_not_found` included, which must not read as an old agent.
/// The two string-detail 404s differ only in FastAPI's text: the agent answers
/// a session lookup miss with `Session '<id>' not found`, whereas an agent that
/// predates the routes answers a relay `Unknown proxy route` or FastAPI's plain
/// `Not Found` — so the string is the discriminator.
enum SessionGitError: Error, Equatable, Sendable {
    case agentOutdated
    case sessionGone
    /// `{"detail": {"code": …, "message"?: …}}` from 400 / 409 / 500 / 503 / 504.
    case git(code: String, message: String?)
    case network(String)

    static let codeGitUnavailable = "git_unavailable"
    static let codeGitTimeout = "git_timeout"
    static let codeGitError = "git_error"
    static let codeGitTooOld = "git_too_old"
    static let codeEmptyMessage = "empty_message"
    static let codeIdentityRequired = "identity_required"
    static let codeUnmerged = "unmerged"
    static let codeNothingToCommit = "nothing_to_commit"
    static let codeNoHead = "no_head"
    static let codeHookFailed = "hook_failed"
    static let codeIndexLocked = "index_locked"
    static let codePathspecMismatch = "pathspec_mismatch"
    /// Another fetch / pull / push owns the repository; the message is its job id.
    static let codeJobRunning = "job_running"
    static let codeJobNotFound = "job_not_found"
    /// The rebase refusals; the message is the ref, the state or the path.
    static let codeUnknownRef = "unknown_ref"
    static let codeNoRebase = "no_rebase"
    static let codeConflictMarkers = "conflict_markers"
    /// The conflict routes' refusals; the message is the path.
    static let codeNotConflicted = "not_conflicted"
    static let codeFileChanged = "file_changed"
    static let codeFileBusy = "file_busy"
    static let codeNotRestorable = "not_restorable"
    static let codeUnsupported = "unsupported"
    static let codeTooLarge = "too_large"
    static let codeBadContent = "bad_content"

    /// Classify a thrown error; anything unrelated passes through unchanged.
    static func map(_ error: any Error) -> any Error {
        if error is SessionGitError {
            return error
        }
        if let apiError = error as? APIError {
            if let detail = SessionGitDetail.parse(apiError.errorBody) {
                return SessionGitError.git(code: detail.code, message: detail.message)
            }
            if apiError.code == 404 {
                if let detail = ConflictDetail.stringOrNil(from: apiError.errorBody),
                   detail.hasPrefix("Session '"), detail.hasSuffix("' not found") {
                    return SessionGitError.sessionGone
                }
                return SessionGitError.agentOutdated
            }
            return error
        }
        if error is URLError || error is DecodingError {
            return SessionGitError.network(error.localizedDescription)
        }
        return error
    }

    /// A cancelled request is the caller's own doing, never a state to show.
    static func isCancellation(_ error: any Error) -> Bool {
        error is CancellationError || (error as? URLError)?.code == .cancelled
    }
}

/// The object-shaped `detail` of the git routes' errors; `ConflictDetail`
/// covers the string shape.
enum SessionGitDetail {
    struct Detail: Equatable {
        let code: String
        let message: String?
    }

    static func parse(_ errorBody: String) -> Detail? {
        guard
            let data = errorBody.data(using: .utf8),
            let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let detail = root["detail"] as? [String: Any],
            let code = detail["code"] as? String
        else {
            return nil
        }
        return Detail(code: code, message: detail["message"] as? String)
    }
}
