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

    /// Reversed iOS client id (`GOOGLE_REVERSED_CLIENT_ID` build setting) —
    /// also registered as the `CFBundleURLTypes` scheme that delivers the
    /// OAuth redirect back to the app, so sign-in must not start without it.
    static var googleReversedClientID: String? {
        resolveOptionalSetting(
            Bundle.main.object(forInfoDictionaryKey: "GoogleReversedClientID") as? String
        )
    }

    /// Android `BuildConfig.PRIVACY_POLICY_URL` default parity.
    static let privacyPolicyURL = "https://mobaide.com/privacy.html"

    /// Numeric App Store id the takeover store links are built from (Android:
    /// Play Store deep links keyed by the package id). Placeholder until the
    /// app is listed on the App Store.
    // TODO(iOS launch): replace with the real App Store Connect numeric id.
    static let appStoreID = "0000000000"

    /// App Store links for the IncompatibleServer takeover (Android:
    /// Play Store deep links). `appStoreURL` deep-links into the App Store
    /// app; `appStoreFallbackURL` is the browser-openable web listing.
    static let appStoreURL = "itms-apps://apps.apple.com/app/id\(appStoreID)"
    static let appStoreFallbackURL = "https://apps.apple.com/app/id\(appStoreID)"

    /// "What changed" link on the upgrade takeover (Android parity: the
    /// protocol-docs URL shown next to the store button).
    static let whatsChangedURL = "https://mobaide.com/docs/protocol"

    /// Support surfaces (Android `BuildConfig.SUPPORT_EMAIL` /
    /// `SUPPORT_GITHUB_URL` default parity).
    static let supportEmail = "support@mobaide.com"
    static let supportGitHubURL = "https://github.com/syrax90/mobaide-community/discussions"

    /// Human-facing app version (`CFBundleShortVersionString`, i.e. the Xcode
    /// `MARKETING_VERSION`). Used in the support-email diagnostics block.
    static var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
    }
}
