import Foundation

/// Mirror of Android `CentralApi` (24 routes) plus the `CentralAuthInterceptor`
/// reaction: the user Bearer is attached per call (never injected), and a 401
/// outside the register/login/google paths invalidates the user session before
/// the error propagates — dashboard JWTs have no refresh token.
struct CentralAPI: Sendable {
    static let noInvalidateSuffixes = [
        // Unauthenticated pre-connect probe: a 401 here (captive portal /
        // proxy) must not invalidate a valid user session — the version gate
        // swallows the failure as `.unknown`, so the logout would be silent.
        "/api/version",
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/google",
        "/api/auth/apple",
    ]

    let centralServerURL: String
    let sessionInvalidator: any SessionInvalidating
    let client: HTTPClient

    init(
        centralServerURL: String,
        sessionInvalidator: any SessionInvalidating,
        client: HTTPClient = HTTPClient()
    ) {
        self.centralServerURL = centralServerURL
        self.sessionInvalidator = sessionInvalidator
        self.client = client
    }

    // MARK: - Unauthenticated routes

    func getVersion() async throws -> VersionInfoResponse {
        try await execute(method: "GET", path: "/api/version")
    }

    func register(email: String, password: String) async throws -> UserAuthResponse {
        try await execute(method: "POST", path: "/api/auth/register", body: UserRegisterRequest(email: email, password: password))
    }

    func login(email: String, password: String) async throws -> UserAuthResponse {
        try await execute(method: "POST", path: "/api/auth/login", body: UserLoginRequest(email: email, password: password))
    }

    func loginWithGoogle(idToken: String) async throws -> UserAuthResponse {
        try await execute(method: "POST", path: "/api/auth/google", body: GoogleAuthRequest(id_token: idToken))
    }

    func loginWithApple(idToken: String, nonce: String) async throws -> UserAuthResponse {
        try await execute(method: "POST", path: "/api/auth/apple", body: AppleAuthRequest(id_token: idToken, nonce: nonce))
    }

    func verifyEmail(email: String, code: String, password: String? = nil) async throws -> UserAuthResponse {
        try await execute(
            method: "POST",
            path: "/api/auth/verify-email",
            body: VerifyEmailRequest(email: email, code: code, password: password)
        )
    }

    func resendVerification(email: String) async throws -> ResendVerificationResponse {
        try await execute(method: "POST", path: "/api/auth/resend-verification", body: ResendVerificationRequest(email: email))
    }

    func forgotPassword(email: String) async throws -> ForgotPasswordResponse {
        try await execute(method: "POST", path: "/api/auth/forgot-password", body: ForgotPasswordRequest(email: email))
    }

    func resetPassword(email: String, code: String, newPassword: String) async throws -> ResetPasswordResponse {
        try await execute(method: "POST", path: "/api/auth/reset-password", body: ResetPasswordRequest(email: email, code: code, new_password: newPassword))
    }

    // MARK: - User-token routes

    func listAgents(userToken: String) async throws -> [AgentSummaryResponse] {
        try await execute(method: "GET", path: "/api/agents", bearer: userToken)
    }

    func createAgent(userToken: String, name: String) async throws -> CreateAgentResponse {
        try await execute(method: "POST", path: "/api/agents", body: CreateAgentRequest(name: name), bearer: userToken)
    }

    func reregisterAgent(userToken: String, agentId: String) async throws -> ReRegisterAgentResponse {
        try await execute(method: "POST", path: "/api/agents/\(agentId)/reregister", rawBody: Data(), bearer: userToken)
    }

    func deleteAgent(userToken: String, agentId: String, uninstall: Bool = false) async throws {
        let query = uninstall ? "?uninstall=true" : ""
        try await executeNoContent(method: "DELETE", path: "/api/agents/\(agentId)\(query)", bearer: userToken)
    }

    func requestAccountDeletion(userToken: String, request: DeleteAccountRequest) async throws {
        try await executeNoContent(method: "POST", path: "/api/auth/me/delete", body: request, bearer: userToken)
    }

    func cancelAccountDeletion(userToken: String) async throws -> CancelAccountDeletionResponse {
        try await execute(method: "POST", path: "/api/auth/me/delete/cancel", rawBody: Data(), bearer: userToken)
    }

    func me(userToken: String) async throws -> MeResponse {
        try await execute(method: "GET", path: "/api/auth/me", bearer: userToken)
    }

    func getMobileTokens(userToken: String, agentId: String, request: MobileTokenRequest) async throws -> MobileTokenResponse {
        try await execute(method: "POST", path: "/api/agents/\(agentId)/token", body: request, bearer: userToken)
    }

    func listDevices(userToken: String) async throws -> [DeviceResponse] {
        try await execute(method: "GET", path: "/api/devices", bearer: userToken)
    }

    func revokeDevice(userToken: String, deviceId: String) async throws {
        try await executeNoContent(method: "DELETE", path: "/api/devices/\(deviceId)", bearer: userToken)
    }

    func registerPushToken(userToken: String, deviceId: String, request: RegisterPushTokenRequest) async throws {
        try await executeNoContent(method: "POST", path: "/api/devices/\(deviceId)/push-token", body: request, bearer: userToken)
    }

    func unregisterPushToken(userToken: String, deviceId: String) async throws {
        try await executeNoContent(method: "DELETE", path: "/api/devices/\(deviceId)/push-token", bearer: userToken)
    }

    func claimAgentByPairing(userToken: String, request: PairingClaimRequest) async throws -> PairingClaimResponse {
        try await execute(method: "POST", path: "/api/pairing/claim", body: request, bearer: userToken)
    }

    /// Mint a short pairing code for the operator to retype on the agent host.
    ///
    /// Supersedes this account's previous unredeemed code, so the set of live
    /// codes stays at one — part of what keeps the ~34.6-bit space safe.
    func mintDeviceCode(userToken: String) async throws -> DeviceCodeMintResponse {
        try await execute(method: "POST", path: "/api/pairing/device-code", rawBody: Data(), bearer: userToken)
    }

    func getDeviceCodeStatus(userToken: String, requestId: String) async throws -> DeviceCodeStatusResponse {
        try await execute(method: "GET", path: "/api/pairing/device-code/\(requestId)", bearer: userToken)
    }

    /// Approve a redeemed code, releasing credentials to the waiting agent.
    ///
    /// Throws `APIError` 409 when the target agent belongs to another account
    /// or the relay's trust key no longer matches this account's pin, and 410
    /// once the request has expired or settled.
    func confirmDeviceCode(
        userToken: String,
        requestId: String,
        request: DeviceCodeConfirmRequest
    ) async throws -> PairingClaimResponse {
        try await execute(
            method: "POST",
            path: "/api/pairing/device-code/\(requestId)/confirm",
            body: request,
            bearer: userToken
        )
    }

    func cancelDeviceCode(userToken: String, requestId: String) async throws {
        try await executeNoContent(method: "DELETE", path: "/api/pairing/device-code/\(requestId)", bearer: userToken)
    }

    func getTrustPin(userToken: String) async throws -> TrustPinResponse {
        try await execute(method: "GET", path: "/api/pairing/trust-pin", bearer: userToken)
    }

    func listTemplates(userToken: String) async throws -> [TemplateDto] {
        try await execute(method: "GET", path: "/api/templates", bearer: userToken)
    }

    func createTemplate(userToken: String, name: String, body: String) async throws -> TemplateDto {
        try await execute(method: "POST", path: "/api/templates", body: CreateTemplateRequest(name: name, body: body), bearer: userToken)
    }

    func updateTemplate(userToken: String, templateId: String, update: UpdateTemplateRequest) async throws -> TemplateDto {
        try await execute(method: "PATCH", path: "/api/templates/\(templateId)", body: update, bearer: userToken)
    }

    func deleteTemplate(userToken: String, templateId: String) async throws {
        try await executeNoContent(method: "DELETE", path: "/api/templates/\(templateId)", bearer: userToken)
    }

    func touchTemplate(userToken: String, templateId: String) async throws -> TemplateDto {
        try await execute(method: "POST", path: "/api/templates/\(templateId)/touch", rawBody: Data(), bearer: userToken)
    }

    func listShortcuts(userToken: String) async throws -> [ShortcutDto] {
        try await execute(method: "GET", path: "/api/shortcuts", bearer: userToken)
    }

    /// Server-side upsert on (kind, chord_token): re-POSTing an existing chord
    /// returns the existing row (201 either way), never a conflict.
    func createShortcut(userToken: String, kind: String, chordToken: String, name: String? = nil) async throws -> ShortcutDto {
        try await execute(
            method: "POST",
            path: "/api/shortcuts",
            body: CreateShortcutRequest(kind: kind, chord_token: chordToken, name: name),
            bearer: userToken
        )
    }

    func updateShortcut(userToken: String, shortcutId: String, update: UpdateShortcutRequest) async throws -> ShortcutDto {
        try await execute(method: "PATCH", path: "/api/shortcuts/\(shortcutId)", body: update, bearer: userToken)
    }

    func deleteShortcut(userToken: String, shortcutId: String) async throws {
        try await executeNoContent(method: "DELETE", path: "/api/shortcuts/\(shortcutId)", bearer: userToken)
    }

    func listCustomAgentTypes(userToken: String) async throws -> [CustomAgentTypeDto] {
        try await execute(method: "GET", path: "/api/custom-agent-types", bearer: userToken)
    }

    func createCustomAgentType(userToken: String, name: String, command: String) async throws -> CustomAgentTypeDto {
        try await execute(
            method: "POST",
            path: "/api/custom-agent-types",
            body: CreateCustomAgentTypeRequest(name: name, command: command),
            bearer: userToken
        )
    }

    func updateCustomAgentType(userToken: String, typeId: String, update: UpdateCustomAgentTypeRequest) async throws -> CustomAgentTypeDto {
        try await execute(method: "PATCH", path: "/api/custom-agent-types/\(typeId)", body: update, bearer: userToken)
    }

    func deleteCustomAgentType(userToken: String, typeId: String) async throws {
        try await executeNoContent(method: "DELETE", path: "/api/custom-agent-types/\(typeId)", bearer: userToken)
    }

    // MARK: - Plumbing

    private func baseURL() -> String {
        var base = centralServerURL
        while base.hasSuffix("/") {
            base.removeLast()
        }
        return base
    }

    private func execute<T: Decodable>(
        method: String,
        path: String,
        rawBody: Data? = nil,
        bearer: String? = nil
    ) async throws -> T {
        let (data, response) = try await send(method: method, path: path, body: rawBody, bearer: bearer)
        guard !data.isEmpty else {
            throw APIError(code: response.statusCode, errorBody: "Empty response body")
        }
        return try WireJSON.decoder().decode(T.self, from: data)
    }

    private func execute<T: Decodable, Body: Encodable>(
        method: String,
        path: String,
        body: Body,
        bearer: String? = nil
    ) async throws -> T {
        let encoded = try WireJSON.encoder().encode(body)
        return try await execute(method: method, path: path, rawBody: encoded, bearer: bearer)
    }

    private func executeNoContent(
        method: String,
        path: String,
        bearer: String?
    ) async throws {
        _ = try await send(method: method, path: path, body: nil, bearer: bearer)
    }

    private func executeNoContent<Body: Encodable>(
        method: String,
        path: String,
        body: Body,
        bearer: String?
    ) async throws {
        let encoded = try WireJSON.encoder().encode(body)
        _ = try await send(method: method, path: path, body: encoded, bearer: bearer)
    }

    private func send(
        method: String,
        path: String,
        body: Data?,
        bearer: String?
    ) async throws -> (Data, HTTPURLResponse) {
        let url = "\(baseURL())\(path)"
        guard let requestURL = URL(string: url) else {
            throw APIError(code: -1, errorBody: "Invalid URL: \(url)")
        }
        var request = URLRequest(url: requestURL)
        request.httpMethod = method
        if let bearer {
            request.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await client.send(request)
        guard (200...299).contains(response.statusCode) else {
            if response.statusCode == 401,
               !Self.noInvalidateSuffixes.contains(where: { path.hasSuffix($0) }) {
                await sessionInvalidator.invalidateUserSession()
            }
            throw APIError(code: response.statusCode, errorBody: String(decoding: data, as: UTF8.self))
        }
        return (data, response)
    }
}
