import SwiftUI

/// Mirror of Android `AppNavigation`: the auth state picks the stack root
/// (unauthenticated → Login, otherwise AgentList), and `IncompatibleServer`
/// collapses the entire shell onto the upgrade takeover rendered
/// **outside** the `NavigationStack` — no route renders until the app is
/// updated.
struct RootView: View {
    @Environment(AuthStateModel.self) private var authStateModel
    @Environment(Router.self) private var router

    var body: some View {
        if case .incompatibleServer(let info) = authStateModel.state {
            IncompatibleServerScreen(info: info)
        } else {
            @Bindable var router = router
            NavigationStack(path: $router.path) {
                rootScreen
                    .navigationDestination(for: Route.self) { route in
                        destination(for: route)
                    }
            }
            .onChange(of: authStateModel.state) { previous, next in
                router.handleTransition(from: previous, to: next)
            }
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
