import Foundation

/// Mirror of Android `CentralApi` (23 routes) plus the `CentralAuthInterceptor`
/// reaction: the user Bearer is attached per call (never injected), and a 401
/// outside the register/login/google paths invalidates the user session before
/// the error propagates — dashboard JWTs have no refresh token.
struct CentralAPI: Sendable {
    static let noInvalidateSuffixes = [
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

    func verifyEmail(email: String, code: String) async throws -> UserAuthResponse {
        try await execute(method: "POST", path: "/api/auth/verify-email", body: VerifyEmailRequest(email: email, code: code))
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

    func deleteAgent(userToken: String, agentId: String) async throws {
        try await executeNoContent(method: "DELETE", path: "/api/agents/\(agentId)", bearer: userToken)
    }

    func requestAccountDeletion(userToken: String, request: DeleteAccountRequest) async throws {
        try await executeNoContent(method: "POST", path: "/api/auth/me/delete", body: request, bearer: userToken)
    }

    func cancelAccountDeletion(userToken: String) async throws -> CancelAccountDeletionResponse {
        try await execute(method: "POST", path: "/api/auth/me/delete/cancel", rawBody: Data(), bearer: userToken)
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

    func claimAgentByPairing(userToken: String, request: PairingClaimRequest) async throws -> PairingClaimResponse {
        try await execute(method: "POST", path: "/api/pairing/claim", body: request, bearer: userToken)
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
