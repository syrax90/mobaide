import SwiftUI

/// Mirror of Android `AppNavigation`: the auth state picks the stack root
/// (unauthenticated → Login, otherwise AgentList), and `IncompatibleServer`
/// collapses the entire shell onto the upgrade takeover rendered
/// **outside** the `NavigationStack` — no route renders until the app is
/// updated.
struct RootView: View {
    @Environment(AuthStateModel.self) private var authStateModel
    @Environment(Router.self) private var router
    @Environment(DeepLinkCoordinator.self) private var deepLink

    @State private var toast: ToastMessage?

    var body: some View {
        // Both notification-deep-link signals are observed above the root
        // switch: attached inside a branch, an error published while the
        // upgrade takeover is up would never be read, never be cleared, and
        // never fire again for the same message.
        Group {
            if case .incompatibleServer(let info) = authStateModel.state {
                IncompatibleServerScreen(info: info)
            } else {
                stack
            }
        }
        .toast($toast)
        .onChange(of: deepLink.switchError, initial: true) { _, newValue in
            if let newValue {
                toast = ToastMessage(text: newValue)
                deepLink.clearSwitchError()
            }
        }
        .onChange(of: deepLink.isSwitchingAgent, initial: true) { _, switching in
            // The switch clears the stack and takes a network round trip; say
            // so, or the wait on the agent list reads as another dead tap.
            if switching {
                toast = ToastMessage(text: "Connecting to agent…")
            }
        }
    }

    private var stack: some View {
        @Bindable var router = router
        return NavigationStack(path: $router.path) {
            rootScreen
                .navigationDestination(for: Route.self) { route in
                    destination(for: route)
                }
        }
        .onChange(of: authStateModel.state) { previous, next in
            router.handleTransition(from: previous, to: next)
        }
    }

    @ViewBuilder
    private var rootScreen: some View {
        switch authStateModel.state {
        case .unauthenticated:
            LoginScreen()
        case .userOnly, .fullyConnected, .incompatibleServer:
            AgentListScreen()
        }
    }

    @ViewBuilder
    private func destination(for route: Route) -> some View {
        switch route {
        case .login:
            LoginScreen()
        case .register:
            RegisterScreen()
        case .verifyEmail(let email):
            VerifyEmailScreen(email: email)
        case .agentList:
            AgentListScreen()
        case .sessionList:
            SessionListScreen()
        case .sessionDetail(let sessionId):
            SessionDetailScreen(sessionId: sessionId)
        case .createSession:
            CreateSessionScreen()
        case .createAutoScan(let projectName):
            CreateSessionScreen(autoScanProject: projectName)
        case .settings:
            SettingsScreen()
        case .support:
            SupportScreen()
        case .autoScanDetail(let projectName):
            AutoScanDetailScreen(projectName: projectName)
        case .connections:
            ConnectionsScreen()
        case .projects:
            ProjectsScreen()
        case .gitManagement(let projectName):
            GitScreen(projectName: projectName)
        case .sessionGitChanges(let sessionId, let commit, let sync, let rebase):
            GitChangesScreen(sessionId: sessionId, openCommit: commit, openSync: sync, openRebase: rebase)
        case .sessionGitDiff(let sessionId, let path, let scope, let origPath):
            GitDiffScreen(sessionId: sessionId, path: path, scope: scope, origPath: origPath)
        case .incompatibleServer:
            // Rendered outside the stack via the takeover above; the route
            // only exists for Android route-table parity.
            EmptyView()
        case .templates:
            TemplatesScreen()
        case .devices:
            DevicesScreen()
        case .forgotPassword:
            ForgotPasswordScreen()
        case .resetPassword(let email):
            ResetPasswordScreen(email: email)
        }
    }
}
