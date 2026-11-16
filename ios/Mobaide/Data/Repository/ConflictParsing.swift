import Foundation

/// Raised when the agent rejects a workspace create/update/delete with 409 —
/// duplicate name, rename collision, or an active auto-scan blocking the change.
/// Android `WorkspaceConflictException`; `detail` is the server's `detail` string.
struct WorkspaceConflictError: Error, Equatable, Sendable {
    let detail: String
}

/// Parsers for FastAPI's `{"detail": …}` 409 bodies. Workspaces wrap a human
/// string; git busy-conflicts wrap `{"busy_by": [...]}`. Android
/// `WorkspacesRepository.parseDetail` / `GitRepository.parseBusyBy` parity.
enum ConflictDetail {
    /// The `detail` string of a workspace conflict, falling back to the raw body
    /// and then a generic label (Android `parseDetail`).
    static func string(from errorBody: String) -> String {
        guard let detail = stringOrNil(from: errorBody) else {
            return errorBody.isEmpty ? "Conflict" : errorBody
        }
        return detail
    }

    /// The `detail` string, or nil when the body is not that shape — for
    /// callers with their own fallback message (Android `extractDetail`).
    static func stringOrNil(from errorBody: String) -> String? {
        guard
            let data = errorBody.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let detail = object["detail"] as? String
        else {
            return nil
        }
        return detail
    }

    /// The busy holders of a git-delete 409, nested under `detail`
    /// (`{"detail": {"busy_by": [...]}}`); nil when the body is not that shape.
    static func busyHolders(from errorBody: String) -> [BusyHolder]? {
        struct Envelope: Decodable { let detail: BusyByResponse }
        guard let data = errorBody.data(using: .utf8) else { return nil }
        return try? WireJSON.decoder().decode(Envelope.self, from: data).detail.busy_by
    }
}
