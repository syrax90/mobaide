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

    /// Always asks for the remote-tracking rows: the flag shields *older
    /// clients* from a kind they would offer for delete, and an older agent
    /// ignores it.
    func getGitItems(projectName: String) async throws -> GitItemsResponse {
        let name = Self.encodePathSegment(projectName)
        return try await execute(method: "GET", url: "\(try await base())/api/projects/\(name)/git/items?remote=true")
    }

    func createGitBranch(projectName: String, request: GitBranchCreateRequest) async throws -> GitBranchResponse {
        let name = Self.encodePathSegment(projectName)
        return try await execute(method: "POST", url: "\(try await base())/api/projects/\(name)/git/branches", body: request)
    }

    func checkoutGitRef(projectName: String, request: GitCheckoutRequest) async throws -> GitBranchResponse {
        let name = Self.encodePathSegment(projectName)
        return try await execute(method: "POST", url: "\(try await base())/api/projects/\(name)/git/checkout", body: request)
    }

    func deleteGitItems(projectName: String, request: GitDeleteRequest) async throws -> GitDeleteResponse {
        let name = Self.encodePathSegment(projectName)
        return try await execute(method: "POST", url: "\(try await base())/api/projects/\(name)/git/items:delete", body: request)
    }

    /// `GET /api/sessions/{id}/git/status?etag=` — the session-scoped Changes
    /// list. A matching `etag` answers `200 {unchanged: true}` (never 304, which
    /// `execute` would reject as an empty body), so the caller keeps its cached
    /// body.
    func getSessionGitStatus(sessionId: String, etag: String? = nil) async throws -> GitStatusResponse {
        let query = etag.map { "?etag=\(Self.encodeQueryValue($0))" } ?? ""
        return try await execute(method: "GET", url: "\(try await base())/api/sessions/\(sessionId)/git/status\(query)")
    }

    /// `GET /api/sessions/{id}/git/diff?path=&scope=&context=&orig_path=`.
    /// `origPath` is the status entry's rename source; without it a
    /// pathspec-limited `git diff -M` cannot see the rename.
    func getSessionGitDiff(
        sessionId: String,
        path: String,
        scope: String,
        context: Int = 3,
        origPath: String? = nil
    ) async throws -> GitDiffResponse {
        var query = "?path=\(Self.encodeQueryValue(path))&scope=\(Self.encodeQueryValue(scope))&context=\(context)"
        if let origPath {
            query += "&orig_path=\(Self.encodeQueryValue(origPath))"
        }
        return try await execute(method: "GET", url: "\(try await base())/api/sessions/\(sessionId)/git/diff\(query)")
    }

    /// `POST /api/sessions/{id}/git/stage` — `git add -A` limited to `paths`.
    func stageSessionGit(sessionId: String, paths: [String]) async throws {
        let _: GitMutationResponse = try await execute(
            method: "POST",
            url: "\(try await base())/api/sessions/\(sessionId)/git/stage",
            body: GitPathsRequest(paths: paths)
        )
    }

    /// `POST /api/sessions/{id}/git/unstage` — a staged rename needs both its paths.
    func unstageSessionGit(sessionId: String, paths: [String]) async throws {
        let _: GitMutationResponse = try await execute(
            method: "POST",
            url: "\(try await base())/api/sessions/\(sessionId)/git/unstage",
            body: GitPathsRequest(paths: paths)
        )
    }

    /// `POST /api/sessions/{id}/git/discard` — throws working-tree changes away;
    /// the caller confirms first.
    func discardSessionGit(sessionId: String, paths: [String], scope: String) async throws {
        let _: GitMutationResponse = try await execute(
            method: "POST",
            url: "\(try await base())/api/sessions/\(sessionId)/git/discard",
            body: GitDiscardRequest(paths: paths, scope: scope)
        )
    }

    /// `GET /api/sessions/{id}/git/commit/context` — author identity and the
    /// previous commit for the commit sheet.
    func getSessionGitCommitContext(sessionId: String) async throws -> GitCommitContextResponse {
        try await execute(method: "GET", url: "\(try await base())/api/sessions/\(sessionId)/git/commit/context")
    }

    /// `POST /api/sessions/{id}/git/commit` — commits the staged set only.
    func commitSessionGit(sessionId: String, request: GitCommitRequest) async throws -> GitCommitResponse {
        try await execute(method: "POST", url: "\(try await base())/api/sessions/\(sessionId)/git/commit", body: request)
    }

    /// `POST /api/sessions/{id}/git/fetch|pull|push` — starts a job; the answer
    /// is its first snapshot, not its result.
    func startSessionGitJob(
        sessionId: String,
        op: String,
        pullMode: String = GitPullRequest.modeFFOnly,
        force: Bool = false
    ) async throws -> GitJobResponse {
        let prefix = "\(try await base())/api/sessions/\(sessionId)/git"
        switch op {
        case GitJobResponse.opFetch:
            return try await execute(method: "POST", url: "\(prefix)/fetch", rawBody: Data())
        case GitJobResponse.opPull:
            return try await execute(method: "POST", url: "\(prefix)/pull", body: GitPullRequest(mode: pullMode))
        default:
            return try await execute(method: "POST", url: "\(prefix)/push", body: GitPushRequest(force: force))
        }
    }

    /// `POST /api/sessions/{id}/git/rebase` — runs the rebase to completion or its first stop.
    func rebaseSessionGit(sessionId: String, onto: String) async throws -> GitRebaseResponse {
        try await execute(
            method: "POST",
            url: "\(try await base())/api/sessions/\(sessionId)/git/rebase",
            body: GitRebaseRequest(onto: onto)
        )
    }

    /// `POST /api/sessions/{id}/git/rebase/continue|skip|abort`.
    func stepSessionGitRebase(sessionId: String, step: String) async throws -> GitRebaseResponse {
        try await execute(
            method: "POST",
            url: "\(try await base())/api/sessions/\(sessionId)/git/rebase/\(step)",
            rawBody: Data()
        )
    }

    /// `GET /api/fs/dirs?path=[&q=]` — subdirectory names for the path fields' suggestions.
    func listDirs(path: String, q: String?) async throws -> FsDirsResponse {
        var query = "?path=\(Self.encodeQueryValue(path))"
        if let q { query += "&q=\(Self.encodeQueryValue(q))" }
        return try await execute(method: "GET", url: "\(try await base())/api/fs/dirs\(query)")
    }

    /// `GET /api/sessions/{id}/git/conflict?path=` — one conflicted file for the resolver.
    func getSessionGitConflict(sessionId: String, path: String) async throws -> GitConflictResponse {
        let query = "?path=\(Self.encodeQueryValue(path))"
        return try await execute(
            method: "GET",
            url: "\(try await base())/api/sessions/\(sessionId)/git/conflict\(query)"
        )
    }

    /// `POST /api/sessions/{id}/git/conflict/resolve` — writes the file and,
    /// unless it is a draft, marks it resolved.
    func resolveSessionGitConflict(
        sessionId: String,
        path: String,
        token: String,
        content: String,
        markResolved: Bool
    ) async throws {
        let _: GitMutationResponse = try await execute(
            method: "POST",
            url: "\(try await base())/api/sessions/\(sessionId)/git/conflict/resolve",
            body: GitConflictResolveRequest(
                path: path, token: token, content: content, mark_resolved: markResolved
            )
        )
    }

    /// `POST /api/sessions/{id}/git/conflict/take` — `side` is `ours` or `theirs`.
    func takeSessionGitConflict(sessionId: String, path: String, side: String) async throws {
        let _: GitMutationResponse = try await execute(
            method: "POST",
            url: "\(try await base())/api/sessions/\(sessionId)/git/conflict/take",
            body: GitConflictTakeRequest(path: path, side: side)
        )
    }

    /// `POST /api/sessions/{id}/git/conflict/restore` — rebuilds the conflict; a
    /// `token` refuses it once the file moved on under an open editor.
    func restoreSessionGitConflict(
        sessionId: String,
        path: String,
        style: String = GitConflictRestoreRequest.styleMerge,
        token: String? = nil
    ) async throws {
        let _: GitMutationResponse = try await execute(
            method: "POST",
            url: "\(try await base())/api/sessions/\(sessionId)/git/conflict/restore",
            body: GitConflictRestoreRequest(path: path, style: style, token: token)
        )
    }

    /// `GET /api/sessions/{id}/git/jobs/{job_id}` — the reconcile poll's snapshot.
    func getSessionGitJob(sessionId: String, jobId: String) async throws -> GitJobResponse {
        try await execute(method: "GET", url: "\(try await base())/api/sessions/\(sessionId)/git/jobs/\(jobId)")
    }

    /// `POST /api/sessions/{id}/git/jobs/{job_id}/cancel` — kills the job's git tree.
    func cancelSessionGitJob(sessionId: String, jobId: String) async throws -> GitJobResponse {
        try await execute(
            method: "POST",
            url: "\(try await base())/api/sessions/\(sessionId)/git/jobs/\(jobId)/cancel",
            rawBody: Data()
        )
    }

    // MARK: - Voice input

    func getVoiceStatus() async throws -> VoiceStatusResponse {
        try await execute(method: "GET", url: "\(try await base())/api/voice/status")
    }

    func installVoice() async throws -> VoiceStatusResponse {
        try await execute(method: "POST", url: "\(try await base())/api/voice/install", body: VoiceInstallRequest())
    }

    func cancelVoiceInstall() async throws -> VoiceStatusResponse {
        try await execute(method: "POST", url: "\(try await base())/api/voice/install/cancel", rawBody: Data("{}".utf8))
    }

    func removeVoice() async throws -> VoiceStatusResponse {
        try await execute(method: "DELETE", url: "\(try await base())/api/voice/model")
    }

    func createVoiceDictation(sessionId: String?) async throws -> VoiceDictationResponse {
        try await execute(
            method: "POST",
            url: "\(try await base())/api/voice/dictations",
            body: VoiceDictationCreateRequest(session_id: sessionId)
        )
    }

    func appendVoiceAudio(dictationId: String, seq: Int, pcm16B64: String) async throws {
        try await executeRaw(
            method: "POST",
            url: "\(try await base())/api/voice/dictations/\(Self.encodePathSegment(dictationId))/audio",
            body: VoiceDictationAudioRequest(seq: seq, pcm16_b64: pcm16B64)
        )
    }

    func finishVoiceDictation(dictationId: String, lastSeq: Int) async throws -> VoiceDictationResultResponse {
        try await execute(
            method: "POST",
            url: "\(try await base())/api/voice/dictations/\(Self.encodePathSegment(dictationId))/finish",
            body: VoiceDictationFinishRequest(last_seq: lastSeq)
        )
    }

    /// 204 with no body, hence the raw path (`execute` refuses an empty body).
    func deleteVoiceDictation(dictationId: String) async throws {
        try await executeRaw(
            method: "DELETE",
            url: "\(try await base())/api/voice/dictations/\(Self.encodePathSegment(dictationId))"
        )
    }

    func cleanupVoiceTranscript(text: String, kind: String) async throws -> VoiceCleanupResponse {
        try await execute(
            method: "POST",
            url: "\(try await base())/api/voice/cleanup",
            body: VoiceCleanupRequest(text: text, kind: kind)
        )
    }

    func setVoiceCleanup(enabled: Bool) async throws -> VoiceStatusResponse {
        try await execute(
            method: "PUT",
            url: "\(try await base())/api/voice/cleanup/settings",
            body: VoiceCleanupSettingsRequest(enabled: enabled)
        )
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

    /// Query values take the same strict set: Starlette's `parse_qsl` reads a
    /// raw `+` as a space and `&`/`#` end the value, so all of them must
    /// percent-encode (OkHttp `addQueryParameter` parity on Android).
    static func encodeQueryValue(_ value: String) -> String {
        encodePathSegment(value)
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
