import Foundation

struct PairingClaimRequest: Codable, Equatable, Sendable {
    let nonce: String
    let trust_key_fp: String
    let mobile_device_id: String
    let mobile_device_name: String?
    let platform: String?
    let hostname_hint: String?
    let friendly_name: String?

    init(
        nonce: String,
        trust_key_fp: String,
        mobile_device_id: String,
        mobile_device_name: String? = nil,
        platform: String? = nil,
        hostname_hint: String? = nil,
        friendly_name: String? = nil
    ) {
        self.nonce = nonce
        self.trust_key_fp = trust_key_fp
        self.mobile_device_id = mobile_device_id
        self.mobile_device_name = mobile_device_name
        self.platform = platform
        self.hostname_hint = hostname_hint
        self.friendly_name = friendly_name
    }
}

/// `ws_delivered` is tri-state: true = pushed to the agent WS at claim time,
/// false = staged in the recovery slot, nil = older central-server build
/// predating the field. Never collapse nil into false.
struct PairingClaimResponse: Codable, Equatable, Sendable {
    let agent_id: String
    let agent_friendly_name: String
    let ws_delivered: Bool?
}

/// Response to `POST /api/pairing/device-code`.
///
/// `request_id` is the handle every follow-up call uses; the code itself never
/// travels in a URL, where the relay's access log would capture it.
struct DeviceCodeMintResponse: Codable, Equatable, Sendable {
    let request_id: String
    let code: String
    let expires_at: String
    let verification_command: String
}

/// Poll state for `GET /api/pairing/device-code/{request_id}`.
///
/// `hostname` and `os` are reported by whichever machine entered the code, over
/// an endpoint that authenticates nobody — they let a user recognise their own
/// host and prove nothing on their own. The control that keeps a stranger's
/// machine out of this account is the confirmation the agent prints on its own
/// terminal, naming this account before it writes credentials.
///
/// `blocked_reason` is set when confirming is already destined to fail, so the
/// UI can say so before the tap rather than burning the code on it.
///
/// `repair_agent_name` is set only when the redeeming host claims an agent this
/// account already owns. Approving then *moves* that agent — rotating its token
/// onto the redeemer and knocking the real host offline — so the confirm screen
/// must name it instead of asking the first-pair question.
struct DeviceCodeStatusResponse: Codable, Equatable, Sendable {
    let status: String
    let expires_at: String
    let hostname: String?
    let os: String?
    let is_repair: Bool
    let repair_agent_name: String?
    let blocked_reason: String?

    // Hand-written so `is_repair` tolerates an omitted key, matching the
    // Kotlin twin's `= false` default and the house pattern for every
    // Kotlin-defaulted boolean (see `GitItem`, `ProjectInfo`). Synthesized
    // `Codable` would throw instead, and the status poll swallows decode
    // errors — so the sheet would sit on "Waiting for a machine…" forever.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decode(String.self, forKey: .status)
        expires_at = try container.decode(String.self, forKey: .expires_at)
        hostname = try container.decodeIfPresent(String.self, forKey: .hostname)
        os = try container.decodeIfPresent(String.self, forKey: .os)
        is_repair = try container.decodeIfPresent(Bool.self, forKey: .is_repair) ?? false
        repair_agent_name = try container.decodeIfPresent(String.self, forKey: .repair_agent_name)
        blocked_reason = try container.decodeIfPresent(String.self, forKey: .blocked_reason)
    }
}

/// Approval body for `POST /api/pairing/device-code/{request_id}/confirm`.
///
/// Carries the same device fields as `PairingClaimRequest` because the relay
/// runs the identical completion path.
struct DeviceCodeConfirmRequest: Codable, Equatable, Sendable {
    let mobile_device_id: String
    let mobile_device_name: String?
    let platform: String?
    let friendly_name: String?

    init(
        mobile_device_id: String,
        mobile_device_name: String? = nil,
        platform: String? = nil,
        friendly_name: String? = nil
    ) {
        self.mobile_device_id = mobile_device_id
        self.mobile_device_name = mobile_device_name
        self.platform = platform
        self.friendly_name = friendly_name
    }
}

/// Response to `GET /api/pairing/trust-pin` — display and comparison only.
///
/// `account_trust_key_fp` must NOT be written into `TrustKeyPinning`.
struct TrustPinResponse: Codable, Equatable, Sendable {
    let relay_url: String
    let relay_trust_key_fp: String
    let account_trust_key_fp: String?
}
