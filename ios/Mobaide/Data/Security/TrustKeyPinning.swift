import Foundation

/// Mirror of Android `TrustKeyPinning` (plain DataStore file
/// "mobaide_trust_pins"): trust-on-first-use pinning of the relay server's
/// manifest signing-key fingerprint. The fingerprint is not secret (it is
/// in the pairing QR and served at `/api/v1/agent/trust_key`), so plain
/// UserDefaults is the right backing store. The pin key is the normalized
/// relay URL; the fingerprint is stored and compared verbatim as an opaque
/// string.
struct TrustKeyPinning: @unchecked Sendable {
    static let suiteName = "mobaide_trust_pins"

    enum VerifyResult: Equatable, Sendable {
        /// No prior pin existed; the scanned fingerprint is now recorded.
        case pinned
        /// The existing pin matches the scanned fingerprint.
        case match
        /// The existing pin disagrees — fail closed, never auto-overwrite.
        case mismatch(pinned: String, scanned: String)
    }

    // UserDefaults is documented thread-safe; this struct holds no other
    // mutable state, hence @unchecked Sendable.
    private let defaults: UserDefaults

    init(defaults: UserDefaults = UserDefaults(suiteName: TrustKeyPinning.suiteName) ?? .standard) {
        self.defaults = defaults
    }

    func getPinned(relayUrl: String) -> String? {
        defaults.string(forKey: Self.normalize(relayUrl))
    }

    func setPinned(relayUrl: String, fingerprint: String) {
        defaults.set(fingerprint, forKey: Self.normalize(relayUrl))
    }

    func verifyOrPin(relayUrl: String, fingerprint: String) -> VerifyResult {
        guard let existing = getPinned(relayUrl: relayUrl) else {
            setPinned(relayUrl: relayUrl, fingerprint: fingerprint)
            return .pinned
        }
        return existing == fingerprint ? .match : .mismatch(pinned: existing, scanned: fingerprint)
    }

    static func normalize(_ relayUrl: String) -> String {
        var normalized = relayUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        while normalized.hasSuffix("/") {
            normalized.removeLast()
        }
        return normalized.lowercased()
    }
}
