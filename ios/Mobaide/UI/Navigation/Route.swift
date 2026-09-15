import Foundation

/// Mirror of Android `Routes.kt` — the navigable destinations, with the
/// parameterized routes carrying their arguments as associated values
/// (no string encoding needed on iOS).
enum Route: Hashable, Sendable {
    case login
    case register
    case verifyEmail(email: String)
    case agentList
    case sessionList
    case sessionDetail(sessionId: String)
    case createSession
    /// The create-session wizard opened straight on Auto Scan config for one
    /// stored project (the Projects screen shortcut). iOS-only — Android has
    /// no equivalent entry point.
    case createAutoScan(projectName: String)
    case settings
    case support
    case autoScanDetail(projectName: String)
    case connections
    case projects
    case gitManagement(projectName: String)
    /// The terminal's `⋯ → Git → Changes…` child screens, scoped to a session
    /// (Android `SESSION_GIT_CHANGES` / `SESSION_GIT_DIFF`).
    /// `commit` presents the commit sheet over the list on arrival (the
    /// terminal menu's Commit…), `sync` the Fetch / Pull / Push sheet (its
    /// Fetch / Pull / Push…).
    case sessionGitChanges(sessionId: String, commit: Bool = false, sync: Bool = false)
    case sessionGitDiff(sessionId: String, path: String, scope: String, origPath: String?)
    case incompatibleServer
    case templates
    case devices
    case forgotPassword
    case resetPassword(email: String)
}
