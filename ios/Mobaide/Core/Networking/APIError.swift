import Foundation

/// Mirror of Android `ApiException(code, errorBody)`: thrown for every
/// non-2xx response with the raw body string ("" when the body is absent).
struct APIError: Error, Equatable, Sendable {
    let code: Int
    let errorBody: String
}

extension APIError: CustomStringConvertible {
    var description: String {
        "HTTP \(code): \(errorBody)"
    }
}
