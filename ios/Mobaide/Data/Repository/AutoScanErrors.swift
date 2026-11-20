import Foundation

/// Typed failures from `AutoScanRepository.startScan` (Android
/// `AutoScanAlreadyRunningException` / `AutoScanCapacityException`), mapped from
/// the agent REST status codes.
enum AutoScanStartError: Error, Equatable, Sendable {
    case alreadyRunning(workspaceName: String)   // HTTP 409
    case capacity(maxScans: Int)               // HTTP 429
    /// HTTP 400: a missing choice or an unusable source or agent; `detail` is
    /// the agent's own words, shown next to the Start button.
    case rejected(detail: String)
}

/// A one-shot user-facing error surfaced by the repository after an optimistic
/// mutation is rolled back (Android `AutoScanUiEvent.Error`).
struct AutoScanUiError: Equatable, Sendable {
    let message: String
}
