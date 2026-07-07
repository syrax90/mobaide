import Foundation

/// Signature-only port of Android `ScanPayloadParser` — the real
/// per-type parsing lands with the autoscan monitor (iOS-07).
enum ScanPayloadParser {
    static func parse(type: String, payload: [String: Any]) -> ScanProgressPayload {
        .unknown(type: type)
    }
}
