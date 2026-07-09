import Foundation

struct DeviceResponse: Codable, Equatable, Sendable {
    let device_id: String
    let name: String?
    let created_at: String
    let last_seen_at: String?
}

/// Body of `POST /api/devices/{device_id}/push-token`.
struct RegisterPushTokenRequest: Codable, Equatable, Sendable {
    let platform: String
    let token: String
    let environment: String
}
