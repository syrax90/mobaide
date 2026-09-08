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
                        : "git refused the request (\(code))."
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
