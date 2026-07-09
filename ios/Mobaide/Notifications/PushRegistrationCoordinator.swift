import Foundation

/// Keeps the server's copy of this device's APNs token fresh.
/// Registration with APNs is requested once per launch as soon as
/// the user is signed in (`.userOnly` or `.fullyConnected` — the push-token
/// endpoint is user-scoped, not agent-scoped); the token is POSTed whenever
/// both a token and an eligible auth state are present and re-POSTed on
/// rotation. `lastPostedToken` is deliberately in-memory only, so every
/// launch re-POSTs once — the server upsert makes that idempotent and it
/// covers tokens rotated while the app was not running.
///
/// A failed POST (offline, or 404 because the `mobile_devices` row is only
/// minted by the token/pairing flows) leaves `lastPostedToken` unset, so the
/// next auth transition — typically into `.fullyConnected`, by which point
/// the row exists — retries.
@MainActor
final class PushRegistrationCoordinator {
    private let authStates: @Sendable () -> AsyncStream<AuthState>
    private let requestRegistration: @MainActor () -> Void
    private let register: @Sendable (String) async throws -> Void
    private let unregisterRequest:
        @MainActor () -> (@Sendable () async throws -> Void)?

    private var authSubscription: Task<Void, Never>?
    private var latestToken: String?
    private var lastPostedToken: String?
    private var didRequestRegistration = false

    private var eligible = false

    init(
        authStates: @escaping @Sendable () -> AsyncStream<AuthState>,
        requestRegistration: @escaping @MainActor () -> Void,
        register: @escaping @Sendable (String) async throws -> Void,
        unregisterRequest:
            @escaping @MainActor () -> (@Sendable () async throws -> Void)?
    ) {
        self.authStates = authStates
        self.requestRegistration = requestRegistration
        self.register = register
        self.unregisterRequest = unregisterRequest
    }

    /// Idempotent; subscribe before the buses start (replay-latest stream
    /// delivers the current state immediately).
    func start() {
        guard authSubscription == nil else { return }
        let states = authStates()
        authSubscription = Task { [weak self] in
            for await state in states {
                await self?.handleAuth(state)
            }
        }
    }

    /// APNs token callback (hex), on first delivery and on rotation.
    func tokenReceived(_ tokenHex: String) {
        latestToken = tokenHex
        Task { await postIfNeeded() }
    }

    /// Best-effort server-side token clear for an explicit sign-out, so the
    /// signed-out device stops receiving the account's attention pushes.
    /// The request factory snapshots the credentials synchronously — before
    /// the caller's logout wipes the keychain — and the DELETE itself is
    /// fire-and-forget: sign-out must never block on the network, and the
    /// user JWT stays server-valid long enough for the in-flight request.
    func handleSignOut() {
        lastPostedToken = nil
        guard let request = unregisterRequest() else { return }
        Task.detached { try? await request() }
    }

    private func handleAuth(_ state: AuthState) async {
        switch state {
        case .userOnly, .fullyConnected:
            eligible = true
            if !didRequestRegistration {
                didRequestRegistration = true
                requestRegistration()
            }
            await postIfNeeded()
        case .unauthenticated:
            eligible = false
            // Next sign-in re-POSTs (possibly a different account onto the
            // same device row).
            lastPostedToken = nil
        case .incompatibleServer:
            eligible = false
        }
    }

    private func postIfNeeded() async {
        guard eligible, let token = latestToken, token != lastPostedToken
        else { return }
        do {
            try await register(token)
            lastPostedToken = token
        } catch {
            // Retried on the next auth transition or token rotation.
        }
    }
}
