import Foundation

/// Port of Android `ScanPayloadParser`: dispatches an autoscan progress event
/// purely on its `type` string into a `ScanProgressPayload`. Field extraction
/// is lenient (missing keys take defaults, never throws), matching the
/// `[String: Any]` handling in `AgentEnvelopeDemux.demuxAutoScanEvent`.
enum ScanPayloadParser {
    static func parse(type: String, payload: [String: Any]) -> ScanProgressPayload {
        switch type {
        case "phase":
            return .phase(
                phase: str(payload, "phase"),
                cycleNumber: int(payload, "cycle_number"),
                nextScanAt: strOrNil(payload, "next_scan_at"),
                issuesProcessed: int(payload, "issues_processed")
            )
        case "issue":
            return .issue(
                identifier: str(payload, "identifier"),
                title: str(payload, "title"),
                step: str(payload, "step"),
                status: str(payload, "status"),
                error: strOrNil(payload, "error")
            )
        case "worktree":
            return .worktree(
                identifier: str(payload, "identifier"),
                path: str(payload, "path")
            )
        case "session":
            return .session(
                identifier: str(payload, "identifier"),
                sessionId: str(payload, "session_id"),
                pid: intOrNil(payload, "pid")
            )
        case "log":
            return .log(
                line: str(payload, "line"),
                level: str(payload, "level", default: "info"),
                ansi: bool(payload, "ansi")
            )
        default:
            return .unknown(type: type)
        }
    }

    private static func str(_ payload: [String: Any], _ key: String, default fallback: String = "") -> String {
        payload[key] as? String ?? fallback
    }

    private static func strOrNil(_ payload: [String: Any], _ key: String) -> String? {
        payload[key] as? String
    }

    private static func int(_ payload: [String: Any], _ key: String) -> Int {
        (payload[key] as? NSNumber)?.intValue ?? 0
    }

    private static func intOrNil(_ payload: [String: Any], _ key: String) -> Int? {
        (payload[key] as? NSNumber)?.intValue
    }

    private static func bool(_ payload: [String: Any], _ key: String) -> Bool {
        payload[key] as? Bool ?? false
    }
}
