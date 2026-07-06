import Foundation

/// `names` must be an exact permutation of existing project names — the
/// server returns 400 otherwise.
struct ProjectOrderRequest: Codable, Equatable, Sendable {
    let names: [String]
}
