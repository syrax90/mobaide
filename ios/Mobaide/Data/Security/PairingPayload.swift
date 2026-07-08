import Foundation

/// Parsed `MOBA1|relay|nonce|fp|exp` QR pairing payload (Android
/// `ParsedQrPairingPayload`). `expUnix` is absolute Unix **seconds**; the
/// expiry comparison lives in the pairing state machine, not the parser.
struct ParsedQrPairingPayload: Equatable, Sendable {
    let version: String
    let relayUrl: String
    let nonce: String
    let trustKeyFp: String
    let expUnix: Int64
}

/// Port of Android `QrPairingScanner.parseMoba1`.
enum PairingPayload {
    /// Parse `MOBA1|relay|nonce|fp|exp`. Returns nil on any malformedness
    /// (wrong field count, unknown version, blank relay/nonce/fingerprint, or a
    /// non-integer expiry). Expiry is not range-checked here.
    static func parseMoba1(_ raw: String) -> ParsedQrPairingPayload? {
        let parts = raw.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
        guard parts.count == 5 else { return nil }
        let (version, relayUrl, nonce, trustKeyFp, expStr) = (parts[0], parts[1], parts[2], parts[3], parts[4])
        guard version == "MOBA1" else { return nil }
        guard !isBlank(relayUrl), !isBlank(nonce), !isBlank(trustKeyFp) else { return nil }
        guard let expUnix = Int64(expStr) else { return nil }
        return ParsedQrPairingPayload(
            version: version,
            relayUrl: relayUrl,
            nonce: nonce,
            trustKeyFp: trustKeyFp,
            expUnix: expUnix
        )
    }

    private static func isBlank(_ value: String) -> Bool {
        value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}
