import Foundation

/// An Allow or Deny tapped on a chat permission alert.
struct PermissionAction: Equatable, Sendable {
    let sessionId: String
    /// The agent that raised a remote alert; nil for a local alert, which is
    /// the connected agent's.
    let agentId: String?
    let requestId: String
    let choice: ChatPermissionChoice
}

/// What the handler needs from the notification layer.
protocol PermissionActionPresenting: AnyObject, Sendable {
    /// Changes on every agent-scoped teardown (sign-out, agent switch).
    func alertEpoch() -> UInt64
    /// The request id the session's shown alert names: nil when no alert of
    /// the session shows, `""` when the alert names no request.
    func shownRequestId(sessionId: String) async -> String?
    func remove(sessionId: String)
    /// Posts nothing once `alertEpoch()` moved past `epoch`.
    func postActionFailure(_ action: PermissionAction, body: String, epoch: UInt64) async
}

extension NotificationService: PermissionActionPresenting {}

/// Sends the answer of a notification action, then removes the alert, or
/// replaces it with one that says why the answer did not go through. Never
/// drops an action silently.
///
/// iOS gives a background action about 30 s and may cold-launch the app for
/// it, so the answer is one REST call built from stored credentials only.
@MainActor
final class PermissionActionHandler {
    /// Leaves room for the failure alert inside the ~30 s iOS allows.
    static let timeoutSeconds: Double = 20

    private let send: @Sendable (PermissionAction) async throws -> Void
    private let presenter: any PermissionActionPresenting

    init(
        send: @escaping @Sendable (PermissionAction) async throws -> Void,
        presenter: any PermissionActionPresenting
    ) {
        self.send = send
        self.presenter = presenter
    }

    /// The agent raises the next request's alert while it answers this one,
    /// so that alert can arrive first: an outcome never removes or replaces
    /// an alert that names another request.
    func handle(_ action: PermissionAction) async {
        let send = self.send
        let epoch = presenter.alertEpoch()
        let body: String
        do {
            let done = try await DictationUploader.withTimeout(Self.timeoutSeconds) {
                try await send(action)
                return true
            }
            if done == true {
                if await presenter.shownRequestId(sessionId: action.sessionId) == action.requestId {
                    presenter.remove(sessionId: action.sessionId)
                }
                return
            }
            body = Self.failureBody(for: nil)
        } catch {
            body = Self.failureBody(for: error)
        }
        if let shown = await presenter.shownRequestId(sessionId: action.sessionId), shown != action.requestId {
            return
        }
        await presenter.postActionFailure(action, body: body, epoch: epoch)
    }

    /// The body of the "Could not answer" alert. Same words as Android's
    /// `PermissionActionReceiver`. nil: no answer in time.
    static func failureBody(for error: (any Error)?) -> String {
        guard let error, !(error is URLError) else {
            return "No connection to your computer. Tap to open."
        }
        // No agent connection is configured any more (Android: the same text).
        if error is AgentURLNotConfiguredError { return "Sign in to Mobaide again. Tap to open." }
        guard let api = error as? APIError else { return "Something went wrong. Tap to open." }
        switch api.code {
        case 409: return "The request was already answered. Tap to open."
        case 404: return "The chat session has ended. Tap to open."
        case 401, 403: return "Sign in to Mobaide again. Tap to open."
        case 503: return "Your computer is offline. Tap to open."
        default: return "Something went wrong. Tap to open."
        }
    }
}

/// Sends the answer to the agent that raised the alert: through the agent
/// connection in use, or, for another agent of the account, with a token
/// minted for that agent alone and not kept.
struct PermissionAnswerSender: Sendable {
    let agentAPI: AgentAPI
    let centralAPI: CentralAPI
    let tokenStore: TokenStore
    let userIdentityStore: UserIdentityStore
    let deviceIdentityStore: DeviceIdentityStore

    func send(_ action: PermissionAction) async throws {
        let body = ChatRespondRequest(request_id: action.requestId, choice: action.choice)
        guard let agentId = action.agentId, agentId != tokenStore.getAgentId() else {
            _ = try await agentAPI.respondToChatRequest(sessionId: action.sessionId, request: body)
            return
        }
        guard let userToken = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: 401, errorBody: "Not signed in")
        }
        let tokens = try await centralAPI.getMobileTokens(
            userToken: userToken,
            agentId: agentId,
            request: MobileTokenRequest(
                mobile_device_id: deviceIdentityStore.getOrCreateDeviceId(),
                mobile_device_name: deviceIdentityStore.getDeviceName(),
                platform: "ios"
            )
        )
        let relay = tokenStore.getRelayServerUrl() ?? AppConfig.centralServerURL
        let api = AgentAPI(
            baseURL: { RelayRouting.agentRESTBaseURL(relayServerURL: relay, agentId: agentId) },
            tokens: MintedAgentToken(value: tokens.access_token),
            client: agentAPI.client
        )
        _ = try await api.respondToChatRequest(sessionId: action.sessionId, request: body)
    }
}

private struct MintedAgentToken: AgentTokenProviding {
    let value: String

    func validToken() async -> AgentToken? { AgentToken(value: value, justRefreshed: true) }

    func refreshedToken() async -> String? { nil }
}
