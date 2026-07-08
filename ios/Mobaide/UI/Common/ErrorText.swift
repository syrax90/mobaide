import Foundation

/// User-facing text for a thrown error: the server's error body for an
/// `APIError` (or a bare `HTTP <code>` when empty), else a generic description.
enum ErrorText {
    static func message(for error: any Error) -> String {
        if let apiError = error as? APIError {
            return apiError.errorBody.isEmpty ? "HTTP \(apiError.code)" : apiError.errorBody
        }
        return error.localizedDescription
    }
}
