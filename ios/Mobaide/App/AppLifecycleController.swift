import SwiftUI
import UIKit

/// Bridges `scenePhase` to the notification pipeline — the honest iOS
/// substitute for Android's foreground service. Backgrounding starts a
/// finite-length background task so the events socket keeps delivering for
/// the ~30 s grace; foregrounding ends it, reconciles missed attention
/// against a fresh REST snapshot, and refreshes the live bus.
@MainActor
final class AppLifecycleController {
    private let coordinator: AttentionCoordinator
    private let agentEventBus: AgentEventBus
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    private var didEnterBackground = false

    init(coordinator: AttentionCoordinator, agentEventBus: AgentEventBus) {
        self.coordinator = coordinator
        self.agentEventBus = agentEventBus
    }

    func handle(_ phase: ScenePhase) {
        switch phase {
        case .background:
            didEnterBackground = true
            beginGrace()
        case .active:
            endGrace()
            // Reconcile/reconnect only after a real background→foreground
            // resume. A transient `.inactive`→`.active` bounce (Control Center,
            // the notification shade, an app-switcher peek) never suspended the
            // app or severed the socket, so re-handshaking there is wasted churn.
            guard didEnterBackground else { return }
            didEnterBackground = false
            // Route through the coordinator's cancellable slot so an agent-leave
            // teardown cancels this reconcile too — otherwise a slow snapshot
            // could re-post the previous agent's alerts after removeAll().
            coordinator.reconcileCancellable()
            Task { [agentEventBus] in await agentEventBus.forceReconnect() }
        default:
            break
        }
    }

    /// Hold the process up briefly so a `session.attention` arriving right
    /// after backgrounding still posts. The expiration handler must return
    /// fast and cannot await — it only ends the task; OS suspension then
    /// severs the socket (rebuilt by `forceReconnect()` on the next foreground).
    private func beginGrace() {
        endGrace()
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "attention-grace") { [weak self] in
            MainActor.assumeIsolated { self?.endGrace() }
        }
    }

    private func endGrace() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }
}
