import Foundation

/// `names` must be an exact permutation of existing workspace names — the
/// server returns 400 otherwise.
struct WorkspaceOrderRequest: Codable, Equatable, Sendable {
    let names: [String]
}
