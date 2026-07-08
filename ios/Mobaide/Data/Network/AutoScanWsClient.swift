import Foundation

/// One inbound frame on the dedicated `ws/autoscan` channel (Android
/// `AutoScanWsClient.Frame`).
enum AutoScanFrame: Equatable, Sendable {
    case connected
    case snapshot(scans: [AutoScanInstanceDto], events: [String: [ParsedScanEvent]])
    case caughtUp
    case event(ParsedScanEvent)
    case disconnected
}

/// A decoded autoscan progress event (Android `AutoScanWsClient.ParsedEvent`).
struct ParsedScanEvent: Equatable, Sendable {
    let projectName: String
    let seq: Int64
    let ts: String
    let type: String
    let payload: ScanProgressPayload
}

/// Dedicated `ws/autoscan` channel client (Android `AutoScanWsClient`).
///
/// `connect` opens one `WebSocketCore` and returns its decoded frames as an
/// `AsyncStream`; cancelling the stream (its `onTermination`) cancels the
/// consume task, which ends the frame loop and then disconnects the socket in
/// program order. `WebSocketCore.disconnect()` does not finish `core.events`,
/// so cancelling the task — not merely disconnecting — is what unblocks the loop.
///
/// **The `resume_from_seq` fix (vs Android).** The per-project wire cursor
/// `lastSeqByProject` is seeded from `resumeFromSeq` and advanced on every
/// inbound event — snapshot-buffered **and** live. The subscribe frame is
/// rebuilt from that **live** cursor on **every** `.connected` (the socket
/// auto-reconnects and re-emits `.connected`), so a reconnect resumes from the
/// true high-water mark instead of the stale value captured at connect time.
/// Inbound events with `seq <= cursor` are dropped (at-least-once replay).
struct AutoScanWsClient: Sendable {
    private let webSocketFactory: WebSocketCoreFactory
    private let urlProvider: @Sendable () async throws -> String?
    private let isAuthenticated: @Sendable () async -> Bool

    init(
        webSocketFactory: WebSocketCoreFactory,
        urlProvider: @escaping @Sendable () async throws -> String?,
        isAuthenticated: @escaping @Sendable () async -> Bool
    ) {
        self.webSocketFactory = webSocketFactory
        self.urlProvider = urlProvider
        self.isAuthenticated = isAuthenticated
    }

    func connect(subscribe: [String], resumeFromSeq: [String: Int64] = [:]) -> AsyncStream<AutoScanFrame> {
        let core = webSocketFactory.make(urlProvider, isAuthenticated)
        return AsyncStream { continuation in
            let task = Task {
                await Self.run(core: core, subscribe: subscribe, resumeFromSeq: resumeFromSeq, continuation: continuation)
            }
            // Only cancel the consume task; `run` disconnects the core in program
            // order after `connect()`, so teardown can never race ahead of connect
            // (which would leak a forever-reconnecting socket on an appear→pop).
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private static func run(
        core: WebSocketCore,
        subscribe: [String],
        resumeFromSeq: [String: Int64],
        continuation: AsyncStream<AutoScanFrame>.Continuation
    ) async {
        var cursor = resumeFromSeq
        let events = core.events
        await core.connect()
        for await event in events {
            switch event {
            case .connected:
                await sendSubscribe(core: core, subscribe: subscribe, cursor: cursor)
                continuation.yield(.connected)
            case .message(let text):
                if let frame = decode(text, cursor: &cursor) {
                    continuation.yield(frame)
                }
            case .disconnected, .error, .tokenRejected, .accountRemoved, .incompatibleServer:
                continuation.yield(.disconnected)
            case .binary:
                break
            }
        }
        continuation.finish()
        // Program-order teardown: runs after `connect()` above even when the
        // task starts already-cancelled (the loop returns immediately), so the
        // socket is always torn down. `disconnect()` is idempotent.
        await core.disconnect()
    }

    private static func sendSubscribe(core: WebSocketCore, subscribe: [String], cursor: [String: Int64]) async {
        var resume: [String: Any] = [:]
        for (project, seq) in cursor { resume[project] = NSNumber(value: seq) }
        let config: [String: Any] = ["subscribe": subscribe, "resume_from_seq": resume]
        guard
            let data = try? JSONSerialization.data(withJSONObject: config),
            let text = String(data: data, encoding: .utf8)
        else { return }
        _ = await core.send(text: text)
    }

    /// Decodes a single frame with a throwaway cursor (no dedup) — the seam
    /// used by golden fixture tests.
    static func decodeFrame(_ text: String) -> AutoScanFrame? {
        var cursor: [String: Int64] = [:]
        return decode(text, cursor: &cursor)
    }

    /// Decodes one text frame, advancing the wire cursor and dropping already
    /// seen events. Returns nil when the frame is malformed or fully deduped.
    private static func decode(_ text: String, cursor: inout [String: Int64]) -> AutoScanFrame? {
        guard
            let object = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any],
            let type = object["type"] as? String
        else { return nil }

        switch type {
        case "snapshot":
            guard let data = object["data"] as? [String: Any] else { return nil }
            let scans = decodeScans(data["scans"])
            var events: [String: [ParsedScanEvent]] = [:]
            if let raw = data["events"] as? [String: Any] {
                for (project, value) in raw {
                    guard let dicts = value as? [[String: Any]] else { continue }
                    let parsed = dicts.compactMap { parseEvent(projectName: project, dict: $0, cursor: &cursor) }
                    if !parsed.isEmpty { events[project] = parsed }
                }
            }
            return .snapshot(scans: scans, events: events)
        case "caught_up":
            return .caughtUp
        case "event":
            let projectName = object["project_name"] as? String ?? ""
            guard
                let dict = object["event"] as? [String: Any],
                let parsed = parseEvent(projectName: projectName, dict: dict, cursor: &cursor)
            else { return nil }
            return .event(parsed)
        default:
            return nil
        }
    }

    private static func parseEvent(
        projectName: String, dict: [String: Any], cursor: inout [String: Int64]
    ) -> ParsedScanEvent? {
        let seq = (dict["seq"] as? NSNumber)?.int64Value ?? 0
        let last = cursor[projectName] ?? 0
        guard seq > last else { return nil }
        cursor[projectName] = seq
        let type = dict["type"] as? String ?? ""
        let payload = dict["payload"] as? [String: Any] ?? [:]
        return ParsedScanEvent(
            projectName: projectName,
            seq: seq,
            ts: dict["ts"] as? String ?? "",
            type: type,
            payload: ScanPayloadParser.parse(type: type, payload: payload)
        )
    }

    private static func decodeScans(_ raw: Any?) -> [AutoScanInstanceDto] {
        guard
            let array = raw as? [[String: Any]],
            let data = try? JSONSerialization.data(withJSONObject: array)
        else { return [] }
        return (try? WireJSON.decoder().decode([AutoScanInstanceDto].self, from: data)) ?? []
    }
}

extension AutoScanWsClient {
    /// Agent-scoped `ws/autoscan` URL provider — a clone of
    /// `AgentEventBus.urlProvider` with the path swapped. Re-evaluated on every
    /// (re)connect attempt so an expiring token is refreshed first.
    static func liveURLProvider(
        tokenStore: TokenStore, sessionManager: SessionManager
    ) -> @Sendable () async throws -> String? {
        {
            guard await sessionManager.isLoggedIn() else { return nil }
            if tokenStore.isAccessTokenExpiringSoon() {
                if await sessionManager.refresh() == .terminal { return nil }
            }
            guard let token = tokenStore.getAccessToken() else { return nil }
            let mode = tokenStore.getConnectionMode()
            let base: String?
            switch mode {
            case .direct: base = tokenStore.getAgentUrl()
            case .relay: base = tokenStore.getRelayServerUrl()
            }
            guard let base else { return nil }
            return RelayRouting.webSocketURLString(
                baseURL: base,
                path: "ws/autoscan",
                mode: mode,
                agentId: tokenStore.getAgentId(),
                token: token
            )
        }
    }

    static func liveIsAuthenticated(sessionManager: SessionManager) -> @Sendable () async -> Bool {
        { await sessionManager.isLoggedIn() }
    }
}
