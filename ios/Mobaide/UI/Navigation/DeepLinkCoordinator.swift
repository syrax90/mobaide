import Foundation
import Observation

/// Stashes a destination that must wait for `.fullyConnected` and replays it
/// into the router on that transition. Two callers: a
/// tapped notification (its session's terminal, possibly on another agent —
/// a cold-start tap can arrive before auth/UI is ready, a warm tap navigates
/// immediately) and the terminal's sessions drawer (another agent's sessions
/// list).
///
/// Replaying against a different connected agent would open a session that
/// agent cannot resolve, so a mismatch connects to the wanted agent first and
/// lets the resulting `fullyConnected` transition drive the navigation.
@MainActor
@Observable
final class DeepLinkCoordinator {
    private let router: Router
    private let authStates: @Sendable () -> AsyncStream<AuthState>
    private let connectedAgentId: @Sendable () -> String?
    private let switchAgent: @Sendable (String) async throws -> Void

    private(set) var pendingRoute: Route?
    private(set) var pendingAgentId: String?

    /// The stashed notification tap's session, when that is what is pending.
    var pendingSessionId: String? {
        if case .sessionDetail(let sessionId) = pendingRoute { return sessionId }
        return nil
    }
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
        stash(.sessionDetail(sessionId: sessionId), agentId: agentId.flatMap { $0.isBlank ? nil : $0 })
    }

    /// The sessions drawer's agent switch: connect to `agentId` and land on
    /// its sessions list.
    func openSessions(ofAgent agentId: String) {
        guard !agentId.isBlank else { return }
        stash(.sessionList, agentId: agentId)
    }

    private func stash(_ route: Route, agentId: String?) {
        pendingRoute = route
        pendingAgentId = agentId
        // A new request supersedes the previous one's verdict, but never cancels
        // a switch already in flight: that call is mid-way through rewriting
        // the agent credentials. It finishes, then replays against these ids.
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
        pendingRoute = nil
        pendingAgentId = nil
        switchAttempted = false
    }

    /// Navigate the pending route once fully connected *and* connected to the
    /// agent it belongs to. Clears the stash *before* navigating so a later
    /// auth re-emit cannot double-navigate, and skips if the session detail is
    /// already on top (Android `launchSingleTop` parity).
    private func replayIfReady() {
        guard latestAuthState == .fullyConnected, let route = pendingRoute else { return }
        if let wanted = pendingAgentId, wanted != connectedAgentId() {
            startAgentSwitch(to: wanted)
            return
        }
        clearPendingTap()
        switch route {
        case .sessionList:
            // The switch cleared the stack onto the agent list; when it did not
            // (same agent, or the transitions coalesced), this still lands on
            // the list rather than stacking a second one.
            router.popTo(.sessionList)
        default:
            if router.path.last == route { return }
            router.navigate(to: route)
        }
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
        switchError = "Couldn’t connect to this agent: "
            + "\(ErrorText.message(for: error)). Pick an agent to reconnect."
    }
}
