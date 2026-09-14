import Foundation

/// Failures of the session-scoped git Changes routes, typed so the screens can
/// tell "update your agent" from "session ended" from "git itself refused"
/// (Android `SessionGitException`).
///
/// The two 404s share a status code and differ only in FastAPI's `detail`: the
/// agent answers a session lookup miss with the string `Session '<id>' not
/// found`, whereas an agent that predates the routes answers a relay `Unknown
/// proxy route` or FastAPI's plain `Not Found` — so the string is the
/// discriminator.
enum SessionGitError: Error, Equatable, Sendable {
    case agentOutdated
    case sessionGone
    /// `{"detail": {"code": …, "message"?: …}}` from 400 / 500 / 503 / 504.
    case git(code: String, message: String?)
    case network(String)

    static let codeGitUnavailable = "git_unavailable"
    static let codeGitTimeout = "git_timeout"
    static let codeGitError = "git_error"

    /// Classify a thrown error; anything unrelated passes through unchanged.
    static func map(_ error: any Error) -> any Error {
        if error is SessionGitError {
            return error
        }
        if let apiError = error as? APIError {
            if apiError.code == 404 {
                if let detail = ConflictDetail.stringOrNil(from: apiError.errorBody),
                   detail.hasPrefix("Session '"), detail.hasSuffix("' not found") {
                    return SessionGitError.sessionGone
                }
                return SessionGitError.agentOutdated
            }
            if let detail = SessionGitDetail.parse(apiError.errorBody) {
                return SessionGitError.git(code: detail.code, message: detail.message)
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
