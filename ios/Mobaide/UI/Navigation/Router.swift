import Observation
import SwiftUI

/// Navigation-stack path owner. The root screen is derived from the auth
/// state in `RootView`; this router only manages the pushed path and the
/// auth-transition reset rules.
@MainActor
@Observable
final class Router {
    var path: [Route] = []

    func navigate(to route: Route) {
        path.append(route)
    }

    func pop() {
        _ = path.popLast()
    }

    func popToRoot() {
        path.removeAll()
    }

    /// Android `AppNavigation` auth-transition parity: any transition into
    /// `unauthenticated` clears the whole stack onto the login root, and an
    /// agent disconnect (`fullyConnected` → `userOnly`) clears it onto the
    /// agent-list root. All other transitions preserve the stack.
    func handleTransition(from previous: AuthState, to next: AuthState) {
        if previous == next { return }
        switch (previous, next) {
        case (_, .unauthenticated):
            path.removeAll()
        case (.fullyConnected, .userOnly):
            path.removeAll()
        default:
            break
        }
    }
}
