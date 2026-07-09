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
