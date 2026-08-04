import Foundation
@testable import Mobaide

/// Records `AttentionPresenting` calls for coordinator/reconcile assertions
/// (NSLock-guarded, mirroring `RecordingAgentStatusApplier`).
final class FakeAttentionPresenter: AttentionPresenting, @unchecked Sendable {
    private let lock = NSLock()
    private var postedStorage: [SessionResponse] = []
    private var removedStorage: [String] = []
    private var authRequestStorage = 0
    private var removeAllStorage = 0
    private var deliveredStorage: Set<String>

    init(delivered: Set<String> = []) {
        deliveredStorage = delivered
    }

    var posted: [SessionResponse] { lock.withLock { postedStorage } }
    var postedIds: [String] { lock.withLock { postedStorage.map(\.session_id) } }
    var removed: [String] { lock.withLock { removedStorage } }
    var authRequestCount: Int { lock.withLock { authRequestStorage } }
    var removeAllCount: Int { lock.withLock { removeAllStorage } }

    /// Overwrite the reported delivered set (used to simulate a live post
    /// interleaving during `reconcile()`'s snapshot await).
    func setDelivered(_ ids: Set<String>) {
        lock.withLock { deliveredStorage = ids }
    }

    func post(_ session: SessionResponse) async {
        lock.withLock { postedStorage.append(session) }
    }

    func remove(sessionId: String) {
        lock.withLock { removedStorage.append(sessionId) }
    }

    func deliveredSessionIds() async -> Set<String> {
        lock.withLock { deliveredStorage }
    }

    func requestAuthorization() {
        lock.withLock { authRequestStorage += 1 }
    }

    func removeAll() {
        lock.withLock { removeAllStorage += 1 }
    }
}

/// Builds a `SessionResponse` for notification/display tests (the wire
/// model's memberwise init defaults only `kind_label` — every other field
/// is required).
func makeSessionResponse(
    id: String = "s1",
    attention: String? = nil,
    title: String? = nil,
    projectName: String? = nil,
    exitReason: String? = nil,
    state: String = "running",
    kind: SessionKind = .claude,
    kindLabel: String? = nil
) -> SessionResponse {
    SessionResponse(
        session_id: id,
        state: state,
        created_at: "2026-01-01T00:00:00Z",
        pid: nil,
        command: [],
        cwd: nil,
        title: title,
        project_name: projectName,
        kind: kind,
        kind_label: kindLabel,
        attention: attention,
        exit_reason: exitReason
    )
}
