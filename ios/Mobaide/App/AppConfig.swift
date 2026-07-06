import Foundation

enum AppConfig {
    /// Central server base URL. Resolution mirrors Android
    /// `build.gradle.kts`: an explicit build-time value wins (Info.plist
    /// `CentralServerURL`, populated from a `CENTRAL_SERVER_URL` build
    /// setting), else the production default.
    static var centralServerURL: String {
        if let configured = Bundle.main.object(forInfoDictionaryKey: "CentralServerURL") as? String,
           !configured.isEmpty {
            return configured
        }
        return "https://api.mobaide.com"
    }

    /// App Store links for the IncompatibleServer takeover (Android:
    /// Play Store deep links). The real store ids land with the version
    /// gate in iOS-08; empty means the takeover hides its store button.
    static let appStoreURL = ""
    static let appStoreFallbackURL = ""
}
