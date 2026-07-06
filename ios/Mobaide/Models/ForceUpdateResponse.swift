import Foundation

/// `outcome` is one of "staged", "deferred", "no_change", "signature_failed".
struct ForceUpdateResponse: Codable, Equatable, Sendable {
    let outcome: String
}
