import Foundation

/// User-facing copy shared by the Changes and Diff screens (Android
/// `unavailableCopy` / `sessionGitErrorCopy`).
enum SessionGitCopy {
    static func unavailable(_ reason: String) -> (title: String, body: String) {
        switch reason {
        case GitStatusResponse.unavailableNotARepo:
            return ("Not a git repository", "The session's folder is not inside a git repository.")
        case GitStatusResponse.unavailableBareRepository:
            return ("Bare repository", "The session runs inside a bare repository, which has no working tree to show.")
        case GitStatusResponse.unavailableCwdMissing:
            return ("Folder not found", "The session's folder no longer exists on the agent host.")
        case GitStatusResponse.unavailableUnsafeDirectory:
            return (
                "Unsafe directory",
                "Git refuses this folder because another user owns it. "
                    + "Allow it with `git config --global --add safe.directory <path>` on the agent host."
            )
        case GitStatusResponse.unavailableGitTooOld:
            return ("Git is too old", "The agent host needs git 2.15 or newer.")
        default:
            return ("Changes unavailable", "The agent could not read this repository (\(reason)).")
        }
    }

    private static let unavailableReasons: Set<String> = [
        GitStatusResponse.unavailableNotARepo,
        GitStatusResponse.unavailableBareRepository,
        GitStatusResponse.unavailableCwdMissing,
        GitStatusResponse.unavailableUnsafeDirectory,
        GitStatusResponse.unavailableGitTooOld,
    ]

    static func errorMessage(for error: any Error) -> String {
        switch error {
        case let sessionGit as SessionGitError:
            switch sessionGit {
            case .git(let code, let message):
                switch code {
                case SessionGitError.codeGitUnavailable:
                    return "git is not installed on the agent host."
                case SessionGitError.codeGitTimeout:
                    return "git took too long to answer. Try again."
                case SessionGitError.codeGitError:
                    return message.map { "git failed: \($0)" } ?? "git failed unexpectedly."
                default:
                    return unavailableReasons.contains(code)
                        ? unavailable(code).body
                        : refusal(code, message: message)
                }
            case .network:
                return "Could not reach the agent. Check the connection and try again."
            case .agentOutdated:
                return "Update your agent to view changes."
            case .sessionGone:
                return "This session has ended."
            }
        case let apiError as APIError:
            return ConflictDetail.stringOrNil(from: apiError.errorBody) ?? "Request failed (HTTP \(apiError.code))."
        default:
            return error.localizedDescription
        }
    }
}

extension SessionGitCopy {
    /// The mutating routes' `409` vocabulary, one line each; the same strings
    /// as Android `refusalCopy` and the web.
    static func refusal(_ code: String, message: String?) -> String {
        switch code {
        case SessionGitError.codeIdentityRequired:
            return "Git doesn't know who you are on the agent machine."
        case SessionGitError.codeUnmerged:
            return "Resolve the conflicts first."
        case SessionGitError.codeNothingToCommit:
            return "Nothing is staged."
        case SessionGitError.codeNoHead:
            return "There is no commit to amend yet."
        case SessionGitError.codeHookFailed:
            return "A commit hook rejected the commit."
        case SessionGitError.codeIndexLocked:
            return "Another git process is running — try again."
        case SessionGitError.codePathspecMismatch:
            return "That file changed under you — refreshed."
        case SessionGitError.codeEmptyMessage:
            return "Enter a commit summary."
        case SessionGitError.codeJobRunning:
            return "Another fetch, pull or push is still running."
        case SessionGitError.codeJobNotFound:
            return GitChangesDisplay.jobLostCopy
        default:
            return message.map { "git refused the request (\(code)): \($0)" }
                ?? "git refused the request (\(code))."
        }
    }

    /// Copy for a failed stage / unstage / discard / commit. One code reads
    /// differently from the polls: the mutating routes need git 2.26, the
    /// reads 2.15, and the agent answers `git_too_old` for both.
    static func mutationMessage(for error: any Error) -> String {
        if case .git(let code, _)? = error as? SessionGitError, code == SessionGitError.codeGitTooOld {
            return "Staging needs git 2.26 or newer on the agent machine."
        }
        return errorMessage(for: error)
    }

    /// The host-side fix shown with `identity_required`, verbatim for copying.
    static let identityFixCommands: [String] = [
        "git config --global user.name \"Your Name\"",
        "git config --global user.email \"you@example.com\"",
    ]
}
