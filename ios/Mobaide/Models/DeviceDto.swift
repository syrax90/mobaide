import Foundation

struct DeviceResponse: Codable, Equatable, Sendable {
    let device_id: String
    let name: String?
    let created_at: String
    let last_seen_at: String?
}
