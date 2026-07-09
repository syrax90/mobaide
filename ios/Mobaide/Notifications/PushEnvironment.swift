import Foundation

/// Detects which APNs environment this install's device tokens belong to, so
/// the server pushes through the matching APNs host. Xcode-installed builds
/// run against the sandbox; TestFlight/App Store builds (no embedded
/// provisioning profile) against production.
enum PushEnvironment {
    static let sandbox = "sandbox"
    static let production = "production"

    static func detect(bundle: Bundle = .main) -> String {
        #if targetEnvironment(simulator)
        return sandbox
        #else
        guard
            let path = bundle.path(
                forResource: "embedded", ofType: "mobileprovision"
            ),
            let text = try? String(contentsOfFile: path, encoding: .isoLatin1)
        else {
            return production
        }
        return parse(provisionText: text) ?? production
        #endif
    }

    /// Extracts `aps-environment` from a provisioning profile's embedded
    /// plist ("development" → sandbox). The profile is CMS-wrapped binary,
    /// but its plist body is plain text — a Latin-1 read plus a regex over
    /// the key/value pair is the standard extraction.
    static func parse(provisionText: String) -> String? {
        let pattern =
            "<key>aps-environment</key>\\s*<string>([^<]+)</string>"
        guard
            let regex = try? NSRegularExpression(pattern: pattern),
            let match = regex.firstMatch(
                in: provisionText,
                range: NSRange(provisionText.startIndex..., in: provisionText)
            ),
            let range = Range(match.range(at: 1), in: provisionText)
        else {
            return nil
        }
        switch provisionText[range] {
        case "development": return sandbox
        case "production": return production
        default: return nil
        }
    }
}
