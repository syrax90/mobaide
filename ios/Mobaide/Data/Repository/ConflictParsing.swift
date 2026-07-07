import Foundation

/// Raised when the agent rejects a project create/update/delete with 409 —
/// duplicate name, rename collision, or an active auto-scan blocking the change.
/// Android `ProjectConflictException`; `detail` is the server's `detail` string.
struct ProjectConflictError: Error, Equatable, Sendable {
    let detail: String
}

/// Parsers for FastAPI's `{"detail": …}` 409 bodies. Projects wrap a human
/// string; git busy-conflicts wrap `{"busy_by": [...]}`. Android
/// `ProjectsRepository.parseDetail` / `GitRepository.parseBusyBy` parity.
enum ConflictDetail {
    /// The `detail` string of a project conflict, falling back to the raw body
    /// and then a generic label (Android `parseDetail`).
    static func string(from errorBody: String) -> String {
        guard
            let data = errorBody.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let detail = object["detail"] as? String
        else {
            return errorBody.isEmpty ? "Conflict" : errorBody
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
