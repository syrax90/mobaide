import Foundation

/// App-scoped autoscan state (Android `AutoScanRepository`). The **list** is
/// push-driven off the shared `ws/events` bus (`autoscan.list.changed` replaces
/// the whole list; `autoscan.status` replaces one instance) with a REST seed;
/// mutations are optimistic and roll back on failure. **Detail** frames come
/// from the dedicated `ws/autoscan` channel via `observeDetailFrames`.
@MainActor
final class AutoScanRepository {
    private let agentAPI: AgentAPI
    private let agentEventBus: AgentEventBus
    private let wsClient: AutoScanWsClient
    private let listBroadcaster = Broadcaster<AutoScanListResponse>.stateFlow()
    private let errorBroadcaster = Broadcaster<AutoScanUiError>()

    private(set) var currentList = AutoScanListResponse()
    private var subscription: Task<Void, Never>?

    init(agentAPI: AgentAPI, agentEventBus: AgentEventBus, wsClient: AutoScanWsClient) {
        self.agentAPI = agentAPI
        self.agentEventBus = agentEventBus
        self.wsClient = wsClient
        listBroadcaster.emit(currentList)
    }

    var currentScans: [AutoScanInstanceDto] { currentList.scans }
    var autoScanPrompt: String? { currentList.auto_scan_prompt }

    func list() -> AsyncStream<AutoScanListResponse> { listBroadcaster.subscribe() }

    func scans() -> AsyncStream<[AutoScanInstanceDto]> {
        let base = listBroadcaster.subscribe()
        return AsyncStream { continuation in
            let task = Task {
                for await list in base { continuation.yield(list.scans) }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    func errors() -> AsyncStream<AutoScanUiError> { errorBroadcaster.subscribe(replayLatest: false) }

    /// Idempotent. Subscribe before `AgentEventBus.start()` so the snapshot the
    /// bus re-pushes on connect is not missed (the events stream has no replay).
    func start() {
        guard subscription == nil else { return }
        let stream = agentEventBus.events()
        subscription = Task { [weak self] in
            for await event in stream {
                await self?.apply(event)
            }
        }
    }

    private func apply(_ event: AgentEvent) {
        switch event {
        case .autoScanListChanged(let list):
            setList(list)
        case .autoScanStatus(let instance):
            let updated = currentList.scans.map { $0.workspace_name == instance.workspace_name ? instance : $0 }
            setScans(updated)
        default:
            break
        }
    }

    @discardableResult
    func refresh() async -> AutoScanListResponse {
        guard let list = try? await agentAPI.getAutoScanList() else { return currentList }
        setList(list)
        return list
    }

    func observeDetailFrames(workspace: String, resume: [String: Int64] = [:]) -> AsyncStream<AutoScanFrame> {
        wsClient.connect(subscribe: [workspace], resumeFromSeq: resume)
    }

    // MARK: - Optimistic mutations (rolled back on failure)

    func startScan(workspaceName: String, claudeOptions: ClaudeOptionsModel? = nil) async throws {
        let previous = currentList
        if !currentList.scans.contains(where: { $0.workspace_name == workspaceName }) {
            let now = Date().timeIntervalSince1970
            let placeholder = AutoScanInstanceDto(
                workspace_name: workspaceName, status: "starting", started_at: now, last_activity_at: now
            )
            setScans(currentList.scans + [placeholder])
        }
        do {
            try await agentAPI.startAutoScan(workspaceName: workspaceName, claudeOptions: claudeOptions)
        } catch let error as APIError where error.code == 409 {
            rollback(previous, message: "Auto scan is already running for \(workspaceName).")
            throw AutoScanStartError.alreadyRunning(workspaceName: workspaceName)
        } catch let error as APIError where error.code == 429 {
            rollback(previous, message: "Auto scan capacity reached (max \(previous.max_scans)).")
            throw AutoScanStartError.capacity(maxScans: previous.max_scans)
        } catch {
            rollback(previous, message: "Failed to start auto scan for \(workspaceName).")
            throw error
        }
    }

    func stopScan(workspaceName: String) async throws {
        let previous = currentList
        setScans(currentList.scans.filter { $0.workspace_name != workspaceName })
        do {
            try await agentAPI.stopAutoScan(workspaceName: workspaceName)
        } catch {
            rollback(previous, message: "Failed to stop auto scan for \(workspaceName).")
            throw error
        }
    }

    @discardableResult
    func runNow(workspaceName: String) async throws -> RunNowResponse {
        let previous = currentList
        setScans(currentList.scans.map { $0.workspace_name == workspaceName ? $0.replacingNextScanAt(nil) : $0 })
        do {
            let response = try await agentAPI.runAutoScanNow(workspaceName: workspaceName)
            setScans(currentList.scans.map {
                $0.workspace_name == workspaceName ? $0.replacingNextScanAt(response.next_scan_at) : $0
            })
            return response
        } catch {
            rollback(previous, message: "Failed to run auto scan now for \(workspaceName).")
            throw error
        }
    }

    private func rollback(_ list: AutoScanListResponse, message: String) {
        setList(list)
        errorBroadcaster.emit(AutoScanUiError(message: message))
    }

    private func setList(_ list: AutoScanListResponse) {
        currentList = list
        listBroadcaster.emit(list)
    }

    private func setScans(_ scans: [AutoScanInstanceDto]) {
        setList(
            AutoScanListResponse(
                scans: scans,
                capacity: currentList.capacity,
                max_scans: currentList.max_scans,
                available_workspaces: currentList.available_workspaces,
                auto_scan_prompt: currentList.auto_scan_prompt
            )
        )
    }
}

private extension AutoScanInstanceDto {
    func replacingNextScanAt(_ nextScanAt: String?) -> AutoScanInstanceDto {
        AutoScanInstanceDto(
            workspace_name: workspace_name,
            status: status,
            started_at: started_at,
            last_activity_at: last_activity_at,
            processed_count: processed_count,
            last_scan_time: last_scan_time,
            scan_interval_seconds: scan_interval_seconds,
            next_scan_at: nextScanAt,
            cycle_number: cycle_number,
            current_seq: current_seq
        )
    }
}
