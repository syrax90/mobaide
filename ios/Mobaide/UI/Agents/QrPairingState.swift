import Foundation

/// QR-pairing state machine (Android `QrPairingState`). Drives the claim UI
/// from scan → TOFU confirm → claim → ≤60 s wait for the agent tunnel to come
/// online. `error` is the only actionable failure state.
enum QrPairingState: Equatable, Sendable {
    case idle
    case confirming(ParsedQrPairingPayload)
    case claiming(ParsedQrPairingPayload)
    case waitingForOnline(agentId: String, friendlyName: String, elapsedSeconds: Int, wsDeliveredAtClaim: Bool?)
    case success(agentId: String, friendlyName: String)
    case error(String)

    /// A non-idle state has an in-flight pairing flow to present.
    var isActive: Bool {
        if case .idle = self { return false }
        return true
    }

    /// The claim POST is in flight — the dialog must not be dismissable.
    var isClaiming: Bool {
        if case .claiming = self { return true }
        return false
    }
}
