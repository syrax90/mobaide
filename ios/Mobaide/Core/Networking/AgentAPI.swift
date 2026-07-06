import Foundation

struct AgentURLNotConfiguredError: Error {}

/// Mirror of Android `RestApi` (21 routes) plus the `AuthInterceptor` policy:
/// Bearer injection is skipped for paths ending in the auth suffixes; a dead
/// session synthesizes a local 401 `{"detail":"Session expired"}` without
/// touching the network; a 401 response triggers exactly one reactive
/// refresh-and-retry unless the token was just pre-emptively refreshed.
struct AgentAPI: Sendable {
    static let skipAuthSuffixes = [
        "/api/auth/token",
        "/api/auth/refresh",
        "/api/mobile/refresh",
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/google",
    ]

    private static let sessionExpiredBody = #"{"detail":"Session expired"}"#

    /// Effective base URL, re-evaluated per call: DIRECT → agent URL,
    /// RELAY → `{relay}/proxy/{agentId}` (Android `TokenStore.getEffectiveBaseUrl`).
    let baseURL: @Sendable () async -> String?
    let tokens: any AgentTokenProviding
    let client: HTTPClient

    init(
        baseURL: @escaping @Sendable () async -> String?,
        tokens: any AgentTokenProviding,
        client: HTTPClient = HTTPClient()
    ) {
        self.baseURL = baseURL
        self.tokens = tokens
        self.client = client
    }

    // MARK: - Routes

    /// The only route that targets a caller-supplied agent URL instead of the
    /// effective base (used before a connection is configured).
    func login(agentUrl: String, username: String, password: String) async throws -> TokenResponse {
        let base = trimTrailingSlashes(agentUrl)
        return try await execute(
            method: "POST",
            url: "\(base)/api/auth/token",
            body: TokenRequest(username: username, password: password)
        )
    }

    func forceAgentUpdate() async throws -> ForceUpdateResponse {
        try await execute(method: "POST", url: "\(try await base())/api/agent/update/force_now", rawBody: Data())
    }

    func getAgentVersion() async throws -> VersionInfoResponse {
        try await execute(method: "GET", url: "\(try await base())/api/version")
    }

    func createSession(_ request: CreateSessionRequest) async throws -> SessionResponse {
        try await execute(method: "POST", url: "\(try await base())/api/sessions", body: request)
    }

    func listSessions() async throws -> [SessionResponse] {
        try await execute(method: "GET", url: "\(try await base())/api/sessions")
    }

    func codexLogin(apiKey: String) async throws -> CodexLoginResponse {
        try await execute(method: "POST", url: "\(try await base())/api/codex/login", body: CodexLoginRequest(api_key: apiKey))
    }

    func deleteSession(sessionId: String, force: Bool = false) async throws {
        let query = force ? "?force=true" : ""
        try await executeRaw(method: "DELETE", url: "\(try await base())/api/sessions/\(sessionId)\(query)")
    }

    func getScrollback(sessionId: String, lines: Int = 1000) async throws -> ScrollbackResponse {
        try await execute(method: "GET", url: "\(try await base())/api/sessions/\(sessionId)/scrollback?lines=\(lines)")
    }

    func getProjects() async throws -> [ProjectInfo] {
        try await execute(method: "GET", url: "\(try await base())/api/projects")
    }

    func createProject(_ project: ProjectWrite) async throws -> ProjectInfo {
        try await execute(method: "POST", url: "\(try await base())/api/projects", body: project)
    }

    func updateProject(currentName: String, update: ProjectUpdate) async throws -> ProjectInfo {
        let name = Self.encodePathSegment(currentName)
        return try await execute(method: "PUT", url: "\(try await base())/api/projects/\(name)", body: update)
    }

    func deleteProject(name: String) async throws {
        let encoded = Self.encodePathSegment(name)
        try await executeRaw(method: "DELETE", url: "\(try await base())/api/projects/\(encoded)")
    }

    func reorderProjects(_ order: ProjectOrderRequest) async throws -> [ProjectInfo] {
        try await execute(method: "PATCH", url: "\(try await base())/api/projects/order", body: order)
    }

    func getLinearLabels(projectName: String) async throws -> [LinearLabelDto] {
        let name = Self.encodePathSegment(projectName)
        return try await execute(method: "GET", url: "\(try await base())/api/projects/\(name)/linear/labels")
    }

    func getLinearStates(projectName: String) async throws -> [LinearStateDto] {
        let name = Self.encodePathSegment(projectName)
        return try await execute(method: "GET", url: "\(try await base())/api/projects/\(name)/linear/states")
    }

    func getGitItems(projectName: String) async throws -> GitItemsResponse {
        let name = Self.encodePathSegment(projectName)
        return try await execute(method: "GET", url: "\(try await base())/api/projects/\(name)/git/items")
    }

    func deleteGitItems(projectName: String, request: GitDeleteRequest) async throws -> GitDeleteResponse {
        let name = Self.encodePathSegment(projectName)
        return try await execute(method: "POST", url: "\(try await base())/api/projects/\(name)/git/items:delete", body: request)
    }

    func getAutoScanList() async throws -> AutoScanListResponse {
        try await execute(method: "GET", url: "\(try await base())/api/autoscan/list")
    }

    func startAutoScan(projectName: String, claudeOptions: ClaudeOptionsModel? = nil) async throws {
        try await executeRaw(
            method: "POST",
            url: "\(try await base())/api/autoscan/start",
            body: StartScanRequest(project_name: projectName, claude_options: claudeOptions)
        )
    }

    func stopAutoScan(projectName: String) async throws {
        try await executeRaw(method: "POST", url: "\(try await base())/api/autoscan/stop", body: StopScanRequest(project_name: projectName))
    }

    func runAutoScanNow(projectName: String) async throws -> RunNowResponse {
        try await execute(method: "POST", url: "\(try await base())/api/autoscan/run-now", body: RunNowRequest(project_name: projectName))
    }

    // MARK: - Plumbing

    /// Java `URLEncoder.encode(name, "UTF-8").replace("+", "%20")` parity:
    /// alphanumerics plus `.-*_` stay literal, everything else (including
    /// spaces and slashes) percent-encodes as UTF-8.
    static func encodePathSegment(_ name: String) -> String {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: ".-*_")
        return name.addingPercentEncoding(withAllowedCharacters: allowed) ?? name
    }

    private func base() async throws -> String {
        guard let base = await baseURL() else {
            throw AgentURLNotConfiguredError()
        }
        return trimTrailingSlashes(base)
    }

    private func trimTrailingSlashes(_ url: String) -> String {
        var trimmed = url
        while trimmed.hasSuffix("/") {
            trimmed.removeLast()
        }
        return trimmed
    }

    private func execute<T: Decodable>(method: String, url: String, rawBody: Data? = nil) async throws -> T {
        let (data, response) = try await send(method: method, url: url, body: rawBody)
        guard !data.isEmpty else {
            throw APIError(code: response.statusCode, errorBody: "Empty response body")
        }
        return try WireJSON.decoder().decode(T.self, from: data)
    }

    private func execute<T: Decodable, Body: Encodable>(method: String, url: String, body: Body) async throws -> T {
        let encoded = try WireJSON.encoder().encode(body)
        return try await execute(method: method, url: url, rawBody: encoded)
    }

    private func executeRaw(method: String, url: String, rawBody: Data? = nil) async throws {
        _ = try await send(method: method, url: url, body: rawBody)
    }

    private func executeRaw<Body: Encodable>(method: String, url: String, body: Body) async throws {
        let encoded = try WireJSON.encoder().encode(body)
        try await executeRaw(method: method, url: url, rawBody: encoded)
    }

    private func send(method: String, url: String, body: Data?) async throws -> (Data, HTTPURLResponse) {
        guard let requestURL = URL(string: url) else {
            throw APIError(code: -1, errorBody: "Invalid URL: \(url)")
        }
        var request = URLRequest(url: requestURL)
        request.httpMethod = method
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await authorizedSend(request)
        guard (200...299).contains(response.statusCode) else {
            throw APIError(code: response.statusCode, errorBody: String(decoding: data, as: UTF8.self))
        }
        return (data, response)
    }

    private func authorizedSend(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let path = request.url?.path ?? ""
        if Self.skipAuthSuffixes.contains(where: { path.hasSuffix($0) }) {
            return try await client.send(request)
        }
        guard let token = await tokens.validToken() else {
            throw APIError(code: 401, errorBody: Self.sessionExpiredBody)
        }
        var authorized = request
        authorized.setValue("Bearer \(token.value)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await client.send(authorized)
        guard response.statusCode == 401, !token.justRefreshed else {
            return (data, response)
        }
        guard let refreshed = await tokens.refreshedToken() else {
            throw APIError(code: 401, errorBody: Self.sessionExpiredBody)
        }
        var retried = request
        retried.setValue("Bearer \(refreshed)", forHTTPHeaderField: "Authorization")
        return try await client.send(retried)
    }
}
