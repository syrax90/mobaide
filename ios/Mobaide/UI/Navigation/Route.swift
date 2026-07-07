import Foundation

/// Mirror of Android `Routes.kt` — the 18 navigable destinations, with the
/// 5 parameterized routes carrying their arguments as associated values
/// (no string encoding needed on iOS).
enum Route: Hashable, Sendable {
    case login
    case register
    case verifyEmail(email: String)
    case agentList
    case sessionList
    case sessionDetail(sessionId: String)
    case createSession
    case settings
    case support
    case autoScanDetail(projectName: String)
    case connections
    case projects
    case gitManagement(projectName: String)
    case incompatibleServer
    case templates
    case devices
    case forgotPassword
    case resetPassword(email: String)
}
