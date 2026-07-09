import Foundation

/// Stashes a tapped notification's session id and replays it into the router
/// once the session is `.fullyConnected` (port of Android's `MainActivity`
/// deep-link stash + `AppNavigation` FullyConnected replay). A cold-start tap
/// can arrive before auth/UI is ready, so the id is held and navigated on the
/// transition; a warm tap navigates immediately.
@MainActor
final class DeepLinkCoordinator {
    private let router: Router
    private let authStates: @Sendable () -> AsyncStream<AuthState>

    private(set) var pendingSessionId: String?
    private var latestAuthState: AuthState
    private var subscription: Task<Void, Never>?

    init(
        router: Router,
        authStates: @escaping @Sendable () -> AsyncStream<AuthState>,
        initialAuthState: AuthState
    ) {
        self.router = router
        self.authStates = authStates
        self.latestAuthState = initialAuthState
    }

    func start() {
        guard subscription == nil else { return }
        let states = authStates()
        subscription = Task { [weak self] in
            for await state in states {
                await self?.onAuthState(state)
            }
        }
    }

    func handleTap(sessionId: String) {
        guard !sessionId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        pendingSessionId = sessionId
        replayIfReady()
    }

    private func onAuthState(_ state: AuthState) {
        latestAuthState = state
        // Drop a stashed tap on logout so it can't replay for the next account.
        if state == .unauthenticated {
            pendingSessionId = nil
        }
        replayIfReady()
    }

    /// Navigate a pending tap once fully connected. Clears the stash *before*
    /// navigating so a later auth re-emit cannot double-navigate, and skips if
    /// the session detail is already on top (Android `launchSingleTop` parity).
    private func replayIfReady() {
        guard latestAuthState == .fullyConnected, let sessionId = pendingSessionId else { return }
        pendingSessionId = nil
        if router.path.last == .sessionDetail(sessionId: sessionId) { return }
        router.navigate(to: .sessionDetail(sessionId: sessionId))
    }
}
