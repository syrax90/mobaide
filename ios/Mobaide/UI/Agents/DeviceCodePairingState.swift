import Foundation

/// The relay's pre-computed verdict on a confirm that would be refused.
///
/// Unknown wire values decode to `nil` rather than throwing: a newer relay may
/// add a reason this build cannot render, and refusing to show the confirm
/// screen at all would be a worse failure than losing the warning and letting
/// the server reject the confirm.
enum DeviceCodeBlockedReason: Equatable, Sendable {
    case unknownAgent
    case ownedByOther

    init?(wire: String?) {
        switch wire {
        case "unknown_agent": self = .unknownAgent
        case "owned_by_other": self = .ownedByOther
        default: return nil
        }
    }

    var message: String {
        switch self {
        case .unknownAgent:
            return "That machine still has credentials for an agent this relay no longer has. "
                + "On the agent host run 'mobaide-agent re-pair --fresh', then enter a new code."
        case .ownedByOther:
            return "That machine is already paired to a different account. Its current owner "
                + "has to remove it before it can be paired here."
        }
    }
}

/// State machine for the reversed pairing direction: this device mints a short
/// code and the operator retypes it on the machine being paired.
///
/// The QR direction needs a camera pointed at the agent's terminal. This one
/// needs nothing to travel *off* the agent host, which is the only thing that
/// works across an SSH jump host, a VM console, or any screen the phone's
/// camera cannot reach.
///
/// The machine ends at `confirming`. Once the confirm lands, the shared tail —
/// waiting for the agent's relay tunnel to actually connect — is handed to
/// `QrPairingState.waitingForOnline`, since the two directions differ only in
/// how they reach that point.
enum DeviceCodePairingState: Equatable, Sendable {
    case idle
    case minting
    /// A code is live and no machine has entered it yet. `command` is the whole
    /// line to run (what the operator copies); `code` is shown separately for
    /// when retyping eight characters beats moving a command between devices.
    case waitingForHost(requestId: String, code: String, command: String)
    /// A machine entered the code and is waiting to be let in.
    ///
    /// `hostname` and `os` were reported by that machine over an endpoint that
    /// authenticates nobody, so they are recognisable, not trustworthy. What
    /// keeps a stranger's machine out of this account is the confirmation the
    /// agent prints on its own terminal, naming this account before it writes
    /// any credentials.
    /// `repairAgentName` is set only when approving would *move* an agent this
    /// account already owns onto the redeeming host, rotating its credentials
    /// and knocking the real machine offline. "Is this your machine?" is the
    /// wrong question in that case.
    case confirming(
        requestId: String,
        hostname: String,
        os: String?,
        repairAgentName: String?,
        blockedReason: DeviceCodeBlockedReason?
    )
    case claiming(requestId: String)
    case error(String)

    var isActive: Bool {
        if case .idle = self { return false }
        return true
    }

    /// The confirm POST is in flight — the sheet must not be dismissable.
    var isClaiming: Bool {
        if case .claiming = self { return true }
        return false
    }

    /// The request id this state is bound to, if any — used to cancel it.
    var requestId: String? {
        switch self {
        case .waitingForHost(let requestId, _, _): return requestId
        case .confirming(let requestId, _, _, _, _): return requestId
        case .claiming(let requestId): return requestId
        case .idle, .minting, .error: return nil
        }
    }
}
