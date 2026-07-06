import XCTest
@testable import Mobaide

final class AgentAPITests: XCTestCase {
    private var client: HTTPClient!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
    }

    private func makeAPI(
        tokens: FakeAgentTokens = FakeAgentTokens(),
        base: String? = "http://agent.example.com"
    ) -> AgentAPI {
        AgentAPI(baseURL: { base }, tokens: tokens, client: client)
    }

    func testBearerInjectedFromValidToken() async throws {
        StubURLProtocol.enqueue(status: 200, body: "[]")
        let tokens = FakeAgentTokens(token: AgentToken(value: "tok"))
        let sessions = try await makeAPI(tokens: tokens).listSessions()

        XCTAssertEqual(sessions, [])
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "http://agent.example.com/api/sessions")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer tok")
        XCTAssertEqual(tokens.validTokenCallCount, 1)
    }

    func testLoginSkipsAuthAndTrimsTrailingSlashes() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"access_token":"a","refresh_token":"r"}"#)
        let tokens = FakeAgentTokens()
        let response = try await makeAPI(tokens: tokens).login(
            agentUrl: "http://agent.example.com//",
            username: "u",
            password: "p"
        )

        XCTAssertEqual(response.token_type, "bearer")
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "http://agent.example.com/api/auth/token")
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(tokens.validTokenCallCount, 0)

        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["username": "u", "password": "p"])
    }

    func testDeadSessionSynthesizes401WithoutNetwork() async {
        let tokens = FakeAgentTokens(token: nil)
        do {
            _ = try await makeAPI(tokens: tokens).listSessions()
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error, APIError(code: 401, errorBody: #"{"detail":"Session expired"}"#))
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testReactive401RefreshesAndRetriesOnce() async throws {
        StubURLProtocol.enqueue(status: 401, body: "expired")
        StubURLProtocol.enqueue(status: 200, body: "[]")
        let tokens = FakeAgentTokens(token: AgentToken(value: "old"), refreshResult: "new")

        let sessions = try await makeAPI(tokens: tokens).listSessions()
        XCTAssertEqual(sessions, [])

        let requests = StubURLProtocol.requests
        XCTAssertEqual(requests.count, 2)
        XCTAssertEqual(requests[0].value(forHTTPHeaderField: "Authorization"), "Bearer old")
        XCTAssertEqual(requests[1].value(forHTTPHeaderField: "Authorization"), "Bearer new")
        XCTAssertEqual(tokens.refreshedTokenCallCount, 1)
    }

    func testNoRetryWhenTokenWasJustRefreshed() async throws {
        StubURLProtocol.enqueue(status: 401, body: "still expired")
        let tokens = FakeAgentTokens(token: AgentToken(value: "fresh", justRefreshed: true), refreshResult: "unused")

        do {
            _ = try await makeAPI(tokens: tokens).listSessions()
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error, APIError(code: 401, errorBody: "still expired"))
        }
        XCTAssertEqual(StubURLProtocol.requests.count, 1)
        XCTAssertEqual(tokens.refreshedTokenCallCount, 0)
    }

    func testFailedReactiveRefreshSynthesizes401() async throws {
        StubURLProtocol.enqueue(status: 401, body: "expired")
        let tokens = FakeAgentTokens(token: AgentToken(value: "old"), refreshResult: nil)

        do {
            _ = try await makeAPI(tokens: tokens).listSessions()
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error, APIError(code: 401, errorBody: #"{"detail":"Session expired"}"#))
        }
        XCTAssertEqual(StubURLProtocol.requests.count, 1)
    }

    func testDeleteSessionAppendsForceQueryOnlyWhenTrue() async throws {
        StubURLProtocol.enqueue(status: 200, body: "")
        StubURLProtocol.enqueue(status: 200, body: "")
        let api = makeAPI()

        try await api.deleteSession(sessionId: "s1")
        try await api.deleteSession(sessionId: "s1", force: true)

        let urls = StubURLProtocol.requests.map(\.url?.absoluteString)
        XCTAssertEqual(urls[0], "http://agent.example.com/api/sessions/s1")
        XCTAssertEqual(urls[1], "http://agent.example.com/api/sessions/s1?force=true")
    }

    func testScrollbackAlwaysCarriesLinesQuery() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"session_id":"s1","lines":[],"total":0}"#)
        _ = try await makeAPI().getScrollback(sessionId: "s1")

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "http://agent.example.com/api/sessions/s1/scrollback?lines=1000")
    }

    func testProjectNamePathSegmentEncoding() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"name":"p","path":"/x"}"#)
        _ = try await makeAPI().updateProject(currentName: "My Project/α", update: ProjectUpdate(conda_env: "ml"))

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "PUT")
        XCTAssertEqual(
            request.url?.absoluteString,
            "http://agent.example.com/api/projects/My%20Project%2F%CE%B1"
        )
    }

    func testUpdateProjectOmitsNilFields() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"name":"p","path":"/x"}"#)
        _ = try await makeAPI().updateProject(currentName: "p", update: ProjectUpdate(linear_api_key: ""))

        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["linear_api_key": ""])
    }

    func testCreateProjectAlwaysSerializesDefaultFlags() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"name":"p","path":"/x"}"#)
        _ = try await makeAPI().createProject(ProjectWrite(name: "p", path: "/x"))

        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["name": "p", "path": "/x", "default_flags": [:]])
    }

    func testForceUpdateSendsEmptyJSONBody() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"outcome":"staged"}"#)
        let response = try await makeAPI().forceAgentUpdate()

        XCTAssertEqual(response.outcome, "staged")
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(StubURLProtocol.bodies[0], Data())
    }

    func testGitItemsDeleteUsesColonSuffixPost() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"results":[]}"#)
        _ = try await makeAPI().deleteGitItems(
            projectName: "proj",
            request: GitDeleteRequest(items: [GitDeleteItem(kind: "branch_only", branch: "b")])
        )

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString, "http://agent.example.com/api/projects/proj/git/items:delete")
    }

    func testNon2xxThrowsAPIErrorWithBody() async {
        StubURLProtocol.enqueue(status: 503, body: "claude_cli_missing")
        do {
            _ = try await makeAPI().createSession(CreateSessionRequest())
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error, APIError(code: 503, errorBody: "claude_cli_missing"))
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testNilBaseURLThrowsNotConfigured() async {
        do {
            _ = try await makeAPI(base: nil).listSessions()
            XCTFail("expected AgentURLNotConfiguredError")
        } catch is AgentURLNotConfiguredError {
        } catch {
            XCTFail("unexpected error \(error)")
        }
    }

    func testSessionUses30SecondTimeout() {
        XCTAssertEqual(HTTPClient.makeSession().configuration.timeoutIntervalForRequest, 30)
    }
}
