import XCTest
@testable import Mobaide

final class CentralAPITests: XCTestCase {
    private var client: HTTPClient!
    private var invalidator: FakeSessionInvalidator!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        invalidator = FakeSessionInvalidator()
    }

    private func makeAPI(base: String = "https://central.example.com") -> CentralAPI {
        CentralAPI(centralServerURL: base, sessionInvalidator: invalidator, client: client)
    }

    func testBearerAttachedPerCall() async throws {
        StubURLProtocol.enqueue(status: 200, body: "[]")
        let agents = try await makeAPI().listAgents(userToken: "user-jwt")

        XCTAssertEqual(agents, [])
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/agents")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer user-jwt")
    }

    func testAuthRoutesSendNoAuthorizationHeader() async throws {
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"access_token":"a","user_id":"u1","email":"e@x.com","role":"user"}"#
        )
        let response = try await makeAPI().login(email: "e@x.com", password: "pw")

        XCTAssertEqual(response.status, "ok")
        XCTAssertEqual(response.token_type, "bearer")
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
    }

    func testAppleAuthRoutePostsTokenAndNonce() async throws {
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"access_token":"a","user_id":"u1","email":"e@x.com","role":"user"}"#
        )
        let response = try await makeAPI().loginWithApple(idToken: "apple-token", nonce: "nonce-1")

        XCTAssertEqual(response.status, "ok")
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/auth/apple")
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
        let body = try WireJSON.decoder().decode(AppleAuthRequest.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, AppleAuthRequest(id_token: "apple-token", nonce: "nonce-1"))
    }

    func test401OnApplePathDoesNotInvalidate() async {
        StubURLProtocol.enqueue(status: 401, body: "Invalid Apple ID token")
        do {
            _ = try await makeAPI().loginWithApple(idToken: "bad", nonce: "n")
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error.code, 401)
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertEqual(invalidator.invalidationCount, 0)
    }

    func testReactive401InvalidatesUserSession() async {
        StubURLProtocol.enqueue(status: 401, body: "token rejected")
        do {
            _ = try await makeAPI().listAgents(userToken: "stale")
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error, APIError(code: 401, errorBody: "token rejected"))
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertEqual(invalidator.invalidationCount, 1)
    }

    func test401OnAuthPathsDoesNotInvalidate() async {
        StubURLProtocol.enqueue(status: 401, body: "bad credentials")
        do {
            _ = try await makeAPI().login(email: "e@x.com", password: "wrong")
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error.code, 401)
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertEqual(invalidator.invalidationCount, 0)
    }

    func testNon401ErrorsDoNotInvalidate() async {
        StubURLProtocol.enqueue(status: 410, body: "nonce consumed")
        do {
            _ = try await makeAPI().claimAgentByPairing(
                userToken: "u",
                request: PairingClaimRequest(nonce: "n", trust_key_fp: "fp", mobile_device_id: "d1")
            )
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error, APIError(code: 410, errorBody: "nonce consumed"))
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertEqual(invalidator.invalidationCount, 0)
    }

    func testNoContentRouteAcceptsEmpty204() async throws {
        StubURLProtocol.enqueue(status: 204, body: "")
        try await makeAPI().deleteAgent(userToken: "u", agentId: "A1")

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "DELETE")
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/agents/A1")
        XCTAssertNil(request.value(forHTTPHeaderField: "Content-Type"))
    }

    func testEmptyBodyPostsCarryJSONContentType() async throws {
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"template_id":"t1","name":"n","body":"b","is_favorite":false,"created_at":"c","updated_at":"u","version":1}"#
        )
        _ = try await makeAPI().touchTemplate(userToken: "u", templateId: "t1")

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/templates/t1/touch")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(StubURLProtocol.bodies[0], Data())
    }

    func testUpdateTemplateUsesPatchAndOmitsNilFields() async throws {
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"template_id":"t1","name":"n","body":"b","is_favorite":true,"created_at":"c","updated_at":"u","version":2}"#
        )
        _ = try await makeAPI().updateTemplate(
            userToken: "u",
            templateId: "t1",
            update: UpdateTemplateRequest(is_favorite: true)
        )

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "PATCH")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["is_favorite": true])
    }

    func testRegisterPushTokenPostsBodyWithBearer() async throws {
        StubURLProtocol.enqueue(status: 204, body: "")
        try await makeAPI().registerPushToken(
            userToken: "user-jwt",
            deviceId: "dev-1",
            request: RegisterPushTokenRequest(
                platform: "ios", token: "a1b2c3", environment: "sandbox"
            )
        )

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(
            request.url?.absoluteString,
            "https://central.example.com/api/devices/dev-1/push-token"
        )
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer user-jwt")
        let body = try WireJSON.decoder().decode(RegisterPushTokenRequest.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(
            body,
            RegisterPushTokenRequest(platform: "ios", token: "a1b2c3", environment: "sandbox")
        )
    }

    func testUnregisterPushTokenDeletes() async throws {
        StubURLProtocol.enqueue(status: 204, body: "")
        try await makeAPI().unregisterPushToken(userToken: "user-jwt", deviceId: "dev-1")

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "DELETE")
        XCTAssertEqual(
            request.url?.absoluteString,
            "https://central.example.com/api/devices/dev-1/push-token"
        )
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer user-jwt")
    }

    // MARK: - Shortcut routes

    private let shortcutRowJSON =
        #"{"shortcut_id":"s1","kind":"claude","chord_token":"ctrl+a","name":null,"position":0,"created_at":"c","updated_at":"u","version":1}"#

    func testListShortcutsGetsWithBearer() async throws {
        StubURLProtocol.enqueue(status: 200, body: "[\(shortcutRowJSON)]")
        let shortcuts = try await makeAPI().listShortcuts(userToken: "user-jwt")

        XCTAssertEqual(shortcuts.map(\.shortcut_id), ["s1"])
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/shortcuts")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer user-jwt")
    }

    func testCreateShortcutOmitsNilNameFromBody() async throws {
        StubURLProtocol.enqueue(status: 201, body: shortcutRowJSON)
        _ = try await makeAPI().createShortcut(userToken: "u", kind: "claude", chordToken: "ctrl+a")

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/shortcuts")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["kind": "claude", "chord_token": "ctrl+a"], "an unnamed save omits name, never sends null")
    }

    func testCreateShortcutWithNameIncludesIt() async throws {
        StubURLProtocol.enqueue(status: 201, body: shortcutRowJSON)
        _ = try await makeAPI().createShortcut(userToken: "u", kind: "codex", chordToken: "ctrl+x", name: "Build")

        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["kind": "codex", "chord_token": "ctrl+x", "name": "Build"])
    }

    func testUpdateShortcutPatchesEmptyStringClearSentinelVerbatim() async throws {
        StubURLProtocol.enqueue(status: 200, body: shortcutRowJSON)
        _ = try await makeAPI().updateShortcut(
            userToken: "u",
            shortcutId: "s1",
            update: UpdateShortcutRequest(name: "")
        )

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "PATCH")
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/shortcuts/s1")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["name": ""], #"clearing the name sends {"name":""} — the server 422s on {}"#)
    }

    func testDeleteShortcutUsesNoContentRoute() async throws {
        StubURLProtocol.enqueue(status: 204, body: "")
        try await makeAPI().deleteShortcut(userToken: "user-jwt", shortcutId: "s1")

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "DELETE")
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/shortcuts/s1")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer user-jwt")
    }

    func testTrailingSlashesTrimmedFromBase() async throws {
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"name":"central","server_version":"1.0.0","protocol_versions":[6],"min_client_version":6}"#
        )
        let version = try await makeAPI(base: "https://central.example.com///").getVersion()

        XCTAssertEqual(version.protocol_versions, [6])
        XCTAssertEqual(version.capabilities, .object([:]))
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://central.example.com/api/version")
    }
}
