import Foundation

enum JWT {
    struct MalformedToken: Error {}

    /// Extract the `exp` claim (seconds since epoch) from a JWT without
    /// signature verification. Returns nil when the token has no `exp`
    /// claim (the server issues non-expiring tokens when
    /// `*_TOKEN_EXPIRE_MINUTES=0`); throws when the token is malformed.
    static func expirationSeconds(of token: String) throws -> Int64? {
        let segments = token.components(separatedBy: ".")
        guard segments.count > 1, let payload = decodeBase64URL(segments[1]) else {
            throw MalformedToken()
        }
        guard let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else {
            throw MalformedToken()
        }
        guard let exp = object["exp"] else { return nil }
        guard let number = exp as? NSNumber else { throw MalformedToken() }
        return number.int64Value
    }

    static func decodeBase64URL(_ string: String) -> Data? {
        var base64 = string
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        while base64.count % 4 != 0 {
            base64.append("=")
        }
        return Data(base64Encoded: base64)
    }
}
