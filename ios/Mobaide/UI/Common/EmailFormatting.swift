import Foundation

enum EmailFormatting {
    /// Mask an email address for display: keep the first character of the
    /// local part and the full domain, replace the middle of the local part
    /// with bullets. Safe for empty input and short local parts (Android
    /// `ui/util/EmailFormatting.kt` parity).
    ///
    /// Examples:
    ///   "alice@example.com"  → "a•••e@example.com"
    ///   "a@example.com"      → "a•••@example.com"
    ///   "ab@example.com"     → "a•@example.com"
    ///   "abc@example.com"    → "a••@example.com"
    ///   ""                   → ""
    static func maskEmail(_ email: String) -> String {
        guard let at = email.firstIndex(of: "@"), at != email.startIndex else {
            return email
        }
        let local = email[..<at]
        let domain = email[at...]
        let masked: String
        switch local.count {
        case ...1:
            masked = local + String(repeating: "•", count: 3)
        case ...3:
            masked = String(local.first!) + String(repeating: "•", count: local.count - 1)
        default:
            masked = String(local.first!)
                + String(repeating: "•", count: local.count - 2)
                + String(local.last!)
        }
        return masked + domain
    }
}
