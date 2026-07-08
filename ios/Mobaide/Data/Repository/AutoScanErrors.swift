import Foundation

/// Typed failures from `AutoScanRepository.startScan` (Android
/// `AutoScanAlreadyRunningException` / `AutoScanCapacityException`), mapped from
/// the agent REST status codes.
enum AutoScanStartError: Error, Equatable, Sendable {
    case alreadyRunning(projectName: String)   // HTTP 409
    case capacity(maxScans: Int)               // HTTP 429
}

/// A one-shot user-facing error surfaced by the repository after an optimistic
/// mutation is rolled back (Android `AutoScanUiEvent.Error`).
struct AutoScanUiError: Equatable, Sendable {
    let message: String
}
