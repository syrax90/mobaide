import FirebaseAnalytics
import FirebaseCore
import Foundation

protocol AnalyticsSink: Sendable {
    var isEnabled: Bool { get }
    func logEvent(_ name: String, parameters: [String: String])
}

/// `isEnabled` is computed on every read: `AppContainer` is built by a
/// `@State` initialiser that runs before `MobaideApp.init()` configures
/// Firebase, and a build without `GoogleService-Info.plist` never configures
/// it at all.
struct FirebaseAnalyticsSink: AnalyticsSink {
    var isEnabled: Bool { FirebaseApp.app() != nil }

    func logEvent(_ name: String, parameters: [String: String]) {
        Analytics.logEvent(name, parameters: parameters)
    }
}
