import Foundation
import Observation

/// Stashes a tapped notification's session id and replays it into the router
/// once the session is `.fullyConnected` (port of Android's `MainActivity`
/// deep-link stash + `AppNavigation` FullyConnected replay). A cold-start tap
/// can arrive before auth/UI is ready, so the id is held and navigated on the
/// transition; a warm tap navigates immediately.
///
/// Replaying one against a different connected agent would open a session that
/// agent cannot resolve, so a mismatch connects to the originating agent first
/// and lets the resulting `fullyConnected` transition drive the navigation.
@MainActor
@Observable
final class DeepLinkCoordinator {
    private let router: Router
    private let authStates: @Sendable () -> AsyncStream<AuthState>
    private let connectedAgentId: @Sendable () -> String?
    private let switchAgent: @Sendable (String) async throws -> Void

    private(set) var pendingSessionId: String?
    private(set) var pendingAgentId: String?
    /// True while connecting to a tapped alert's agent. Surfaced by `RootView`:
    /// the switch clears the navigation stack and takes a network round trip,
    /// and an unexplained wait on the agent list reads as the very "tapping did
    /// nothing" symptom this coordinator exists to fix.
    private(set) var isSwitchingAgent = false
    /// Set when connecting to a tapped alert's agent failed; `RootView`
    /// surfaces it as a toast and clears it.
    private(set) var switchError: String?

    @ObservationIgnored private var latestAuthState: AuthState
    @ObservationIgnored private var subscription: Task<Void, Never>?
    @ObservationIgnored private var switchTask: Task<Void, Never>?
    /// One switch attempt per stashed tap. `switchAgent` cycles the auth state
    /// through `userOnly` back to `fullyConnected` itself, which re-enters
    /// `replayIfReady()` — without this a failing switch would restart forever.
    @ObservationIgnored private var switchAttempted = false

    init(
        router: Router,
        authStates: @escaping @Sendable () -> AsyncStream<AuthState>,
        initialAuthState: AuthState,
        connectedAgentId: @escaping @Sendable () -> String?,
        switchAgent: @escaping @Sendable (String) async throws -> Void
    ) {
        self.router = router
        self.authStates = authStates
        self.latestAuthState = initialAuthState
        self.connectedAgentId = connectedAgentId
        self.switchAgent = switchAgent
    }

    func start() {
        guard subscription == nil else { return }
        let states = authStates()
        subscription = Task { [weak self] in
            for await state in states {
                guard let self else { break }
                self.onAuthState(state)
            }
        }
    }

    func handleTap(sessionId: String, agentId: String?) {
        guard !sessionId.isBlank else { return }
        pendingSessionId = sessionId
        pendingAgentId = agentId.flatMap { $0.isBlank ? nil : $0 }
        // A new tap supersedes the previous one's verdict, but never cancels a
        // switch already in flight: that call is mid-way through rewriting the
        // agent credentials. It finishes, then replays against these ids.
        switchAttempted = false
        replayIfReady()
    }

    func clearSwitchError() { switchError = nil }

    private func onAuthState(_ state: AuthState) {
        latestAuthState = state
        // Drop a stashed tap on logout so it can't replay for the next account.
        if state == .unauthenticated {
            switchTask?.cancel()
            switchTask = nil
            isSwitchingAgent = false
            clearPendingTap()
            switchError = nil
        }
        replayIfReady()
    }

    private func clearPendingTap() {
        pendingSessionId = nil
        pendingAgentId = nil
        switchAttempted = false
    }

    /// Navigate a pending tap once fully connected *and* connected to the agent
    /// that raised it. Clears the stash *before* navigating so a later auth
    /// re-emit cannot double-navigate, and skips if the session detail is
    /// already on top (Android `launchSingleTop` parity).
    private func replayIfReady() {
        guard latestAuthState == .fullyConnected, let sessionId = pendingSessionId else { return }
        if let wanted = pendingAgentId, wanted != connectedAgentId() {
            startAgentSwitch(to: wanted)
            return
        }
        clearPendingTap()
        if router.path.last == .sessionDetail(sessionId: sessionId) { return }
        router.navigate(to: .sessionDetail(sessionId: sessionId))
    }

    private func startAgentSwitch(to agentId: String) {
        guard switchTask == nil, !switchAttempted else { return }
        switchAttempted = true
        isSwitchingAgent = true
        switchTask = Task { [weak self, switchAgent] in
            do {
                try await switchAgent(agentId)
            } catch {
                guard !Task.isCancelled else { return }
                self?.finishSwitch(for: agentId, error: error)
                return
            }
            guard !Task.isCancelled else { return }
            // A successful switch ends in `markAgentConnected()`, so the auth
            // stream has usually replayed already and found the stash empty;
            // this covers the case where it has not.
            self?.finishSwitch(for: agentId, error: nil)
        }
    }

    private func finishSwitch(for agentId: String, error: (any Error)?) {
        switchTask = nil
        isSwitchingAgent = false
        // A tap that landed mid-switch has already replaced the stash. Its ids
        // are the live ones, so re-evaluate for them rather than clearing them
        // on this older switch's verdict.
        guard pendingAgentId == agentId else {
            switchAttempted = false
            replayIfReady()
            return
        }
        guard let error else {
            replayIfReady()
            return
        }
        clearPendingTap()
        // Name the recovery: `connectAgentViaRelay` disconnects before it can
        // fail, so the user is now on the agent list with nothing connected.
        switchError = "Couldn’t connect to this notification’s agent: "
            + "\(ErrorText.message(for: error)). Pick an agent to reconnect."
    }
}
