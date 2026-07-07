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
        resolveOptionalSetting(configured) ?? defaultCentralServerURL
    }

    /// Shared unresolved-`$(…)`-token handling for every Info.plist value
    /// populated from an optional build setting.
    static func resolveOptionalSetting(_ configured: String?) -> String? {
        guard let configured, !configured.isEmpty, !configured.contains("$(") else {
            return nil
        }
        return configured
    }

    /// Google OAuth iOS client id (`GOOGLE_IOS_CLIENT_ID` build setting).
    /// nil when the build is not configured for Google Sign-In.
    static var googleIOSClientID: String? {
        resolveOptionalSetting(
            Bundle.main.object(forInfoDictionaryKey: "GoogleClientID") as? String
        )
    }

    /// Google OAuth web/server client id (`GOOGLE_SERVER_CLIENT_ID` build
    /// setting) — the audience the central server verifies, so it must match
    /// the server's `GOOGLE_CLIENT_ID` (Android `GOOGLE_WEB_CLIENT_ID`
    /// parity).
    static var googleServerClientID: String? {
        resolveOptionalSetting(
            Bundle.main.object(forInfoDictionaryKey: "GoogleServerClientID") as? String
        )
    }

    /// Android `BuildConfig.PRIVACY_POLICY_URL` default parity.
    static let privacyPolicyURL = "https://mobaide.com/privacy.html"

    /// App Store links for the IncompatibleServer takeover (Android:
    /// Play Store deep links). The real store ids land with the version
    /// gate in iOS-08; empty means the takeover hides its store button.
    static let appStoreURL = ""
    static let appStoreFallbackURL = ""
}
