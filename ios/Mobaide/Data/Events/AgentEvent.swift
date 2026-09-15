import Foundation

/// Mirror of Android `AgentEvent`: typed events demuxed from the
/// `{type, payload}` envelopes of the event WebSocket channels.
enum AgentEvent: Equatable, Sendable {
    case sessionsChanged([SessionResponse])
    case sessionUpdated(SessionResponse)
    /// One-shot "needs user" trigger; the reason lives in
    /// `SessionResponse.attention`.
    case sessionAttention(SessionResponse)
    case projectsChanged([ProjectInfo])
    /// `toplevel` / `common_dir` (proto-v7 additive keys) let a session match
    /// a push by repository even when it has no `project_name`.
    case gitChanged(projectName: String, toplevel: String? = nil, commonDir: String? = nil)
    /// A running fetch / pull / push as a whole snapshot: at most four a second
    /// per job, newest counters winning, so a dropped one costs nothing.
    case gitJobProgress(GitJobResponse)
    /// The same job's final snapshot, once; a `git.changed` follows it.
    case gitJobDone(GitJobResponse)
    case autoScanStatus(AutoScanInstanceDto)
    case autoScanListChanged(AutoScanListResponse)
    case autoScanEvent(projectName: String, seq: Int64, ts: String, payload: ScanProgressPayload)
    case agentStatusChanged(agentId: String, online: Bool)
    case templateCreated(TemplateDto)
    case templateUpdated(TemplateDto)
    case templateDeleted(templateId: String)
    case agentOutdated
    case unknownEnvelopeType(type: String, raw: String)
}

/// Mirror of Android `ScanProgressPayload`: inner autoscan progress event
/// payloads. Only the shape lands in this issue — `ScanPayloadParser`
/// stays a stub until iOS-07.
enum ScanProgressPayload: Equatable, Sendable {
    /// `phase` ∈ scan_started | scan_completed | scan_stopped (free-form on the wire).
    case phase(phase: String, cycleNumber: Int, nextScanAt: String?, issuesProcessed: Int)
    /// `step` ∈ state_update | label_removal | worktree_create | session_spawn;
    /// `status` ∈ started | ok | failed (free-form on the wire).
    case issue(identifier: String, title: String, step: String, status: String, error: String?)
    case worktree(identifier: String, path: String)
    case session(identifier: String, sessionId: String, pid: Int?)
    case log(line: String, level: String, ansi: Bool)
    case unknown(type: String)
}

struct SessionsChangedPayload: Codable, Equatable, Sendable {
    let sessions: [SessionResponse]
}

struct SessionUpdatedPayload: Codable, Equatable, Sendable {
    let session: SessionResponse
}

struct ProjectsChangedPayload: Codable, Equatable, Sendable {
    let projects: [ProjectInfo]
}

struct AgentStatusChangedPayload: Codable, Equatable, Sendable {
    let agent_id: String
    let online: Bool
}

struct FirstAgentPairedPayload: Codable, Equatable, Sendable {
    let agent_id: String
}

/// Demux for the agent-scoped `ws/events` channel (Android
/// `AgentEventBus.parseAndEmit`). Pure so tests can drive the full fixture
/// matrix without WebSocket plumbing.
///
/// Drop rules (Android parity): a malformed outer envelope, a payload that
/// is not the expected shape, and a payload that fails type-specific
/// decoding all return nil (message dropped). Only a well-formed envelope
/// with an unrecognized `type` becomes `.unknownEnvelopeType`.
enum AgentEnvelopeDemux {
    static func demux(_ text: String) -> AgentEvent? {
        guard let envelope = EventEnvelope(text: text) else { return nil }
        switch envelope.type {
        case "sessions.changed":
            return decode(SessionsChangedPayload.self, from: envelope).map { .sessionsChanged($0.sessions) }
        case "session.updated":
            return decode(SessionUpdatedPayload.self, from: envelope).map { .sessionUpdated($0.session) }
        case "session.attention":
            return decode(SessionUpdatedPayload.self, from: envelope).map { .sessionAttention($0.session) }
        case "projects.changed":
            return decode(ProjectsChangedPayload.self, from: envelope).map { .projectsChanged($0.projects) }
        case "git.changed":
            guard let payload = envelope.payloadObject else { return nil }
            return .gitChanged(
                projectName: payload["project_name"] as? String ?? "",
                toplevel: payload["toplevel"] as? String,
                commonDir: payload["common_dir"] as? String
            )
        case "git.job.progress":
            return decode(GitJobResponse.self, from: envelope).map { .gitJobProgress($0) }
        case "git.job.done":
            return decode(GitJobResponse.self, from: envelope).map { .gitJobDone($0) }
        case "autoscan.status":
            return decode(AutoScanInstanceDto.self, from: envelope).map { .autoScanStatus($0) }
        case "autoscan.list.changed":
            return decode(AutoScanListResponse.self, from: envelope).map { .autoScanListChanged($0) }
        case "autoscan.event":
            return demuxAutoScanEvent(envelope)
        default:
            return .unknownEnvelopeType(type: envelope.type, raw: text)
        }
    }

    /// `{project_name, event: {seq, ts, type, payload}}` — `event` not being
    /// an object drops the message; every inner field falls back to a
    /// default. `seq` is 64-bit (can exceed 2^53).
    private static func demuxAutoScanEvent(_ envelope: EventEnvelope) -> AgentEvent? {
        guard let payload = envelope.payloadObject else { return nil }
        let projectName = payload["project_name"] as? String ?? ""
        guard let event = payload["event"] as? [String: Any] else { return nil }
        let seq = (event["seq"] as? NSNumber)?.int64Value ?? 0
        let ts = event["ts"] as? String ?? ""
        let type = event["type"] as? String ?? ""
        let innerPayload = event["payload"] as? [String: Any] ?? [:]
        return .autoScanEvent(
            projectName: projectName,
            seq: seq,
            ts: ts,
            payload: ScanPayloadParser.parse(type: type, payload: innerPayload)
        )
    }

    private static func decode<T: Decodable>(_ type: T.Type, from envelope: EventEnvelope) -> T? {
        guard let data = envelope.payloadData else { return nil }
        return try? WireJSON.decoder().decode(T.self, from: data)
    }
}

/// Outer `{type, payload}` envelope shared by both event channels. Both
/// fields are required; a missing/invalid one drops the whole message.
struct EventEnvelope {
    let type: String
    let payloadObject: [String: Any]?

    init?(text: String) {
        guard
            let object = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any],
            let type = object["type"] as? String,
            object["payload"] != nil
        else { return nil }
        self.type = type
        self.payloadObject = object["payload"] as? [String: Any]
    }

    /// Payload re-serialized for `Decodable` payload models; nil when the
    /// payload is not a JSON object.
    var payloadData: Data? {
        guard let payloadObject else { return nil }
        return try? JSONSerialization.data(withJSONObject: payloadObject)
    }
}
