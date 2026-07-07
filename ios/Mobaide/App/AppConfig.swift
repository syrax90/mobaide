import Foundation

enum AppConfig {
    static let defaultCentralServerURL = "https://api.mobaide.com"

    /// Central server base URL. Resolution mirrors Android
    /// `build.gradle.kts`: an explicit build-time value wins (Info.plist
    /// `CentralServerURL`, populated from a `CENTRAL_SERVER_URL` build
    /// setting), else the production default.
    static var centralServerURL: String {
        resolveCentralServerURL(
            Bundle.main.object(forInfoDictionaryKey: "CentralServerURL") as? String
        )
    }

    /// Xcode leaves an undefined build-setting reference in Info.plist as
    /// the literal `$(CENTRAL_SERVER_URL)` token rather than blanking it,
    /// so unresolved placeholders fall back exactly like missing/empty
    /// values.
    static func resolveCentralServerURL(_ configured: String?) -> String {
        guard let configured, !configured.isEmpty, !configured.contains("$(") else {
            return defaultCentralServerURL
        }
        return configured
    }

    /// App Store links for the IncompatibleServer takeover (Android:
    /// Play Store deep links). The real store ids land with the version
    /// gate in iOS-08; empty means the takeover hides its store button.
    static let appStoreURL = ""
    static let appStoreFallbackURL = ""
}
