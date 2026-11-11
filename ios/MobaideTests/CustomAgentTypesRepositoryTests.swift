import XCTest
@testable import Mobaide

/// `CustomAgentTypesRepository` — the custom-agent-type sibling of
/// `ShortcutsRepository`: REST delegation over `StubURLProtocol`, the
/// user-token requirement, the account-generation guard on refresh AND every
/// mutation, and the cache mutations driven both by REST round-trips and by
/// `applyUpserted` / `applyDeleted` (the entrypoints `UserEventBus` calls for
/// `custom_type.*` pushes). Mirrors Android `CustomAgentTypesRepositoryTest`.
@MainActor
final class CustomAgentTypesRepositoryTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeRepo(loggedIn: Bool = true) -> CustomAgentTypesRepository {
        AgentHarness(session: SessionHarness(userLoggedIn: loggedIn)).customAgentTypesRepository
    }

    /// Repo plus its (Sendable) identity store, for tests that flip the
    /// signed-in account while a request is in flight.
    private func makeRepoWithIdentity() -> (repo: CustomAgentTypesRepository, identity: UserIdentityStore) {
        let session = SessionHarness(userLoggedIn: true)
        return (AgentHarness(session: session).customAgentTypesRepository, session.userIdentityStore)
    }

    private func typeJSON(
        id: String = "ab12cd34ef56",
        name: String = "Aider",
        command: String = "aider --model sonnet",
        updatedAt: String = "2026-07-28T12:00:00Z"
    ) -> String {
        #"{"id":"\#(id)","name":"\#(name)","command":"\#(command)","created_at":"2026-07-28T12:00:00Z","updated_at":"\#(updatedAt)"}"#
    }

    // MARK: - Refresh

    func testRefreshReplacesCacheWholesale() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: "[\(typeJSON()),\(typeJSON(id: "ffffffffffff", name: "Goose"))]")
        try await repo.refreshCustomTypes()
        XCTAssertEqual(repo.currentTypes.map(\.id), ["ab12cd34ef56", "ffffffffffff"])
        XCTAssertEqual(StubURLProtocol.requests.first?.url?.path, "/api/custom-agent-types")

        StubURLProtocol.enqueue(status: 200, body: "[\(typeJSON(id: "ffffffffffff", name: "Goose"))]")
        try await repo.refreshCustomTypes()
        XCTAssertEqual(
            repo.currentTypes.map(\.id), ["ffffffffffff"],
            "a refresh replaces the whole snapshot, never merges"
        )
    }

    func testRefreshWhileLoggedOutThrowsWithoutNetwork() async {
        let repo = makeRepo(loggedIn: false)
        do {
            try await repo.refreshCustomTypes()
            XCTFail("expected APIError")
        } catch let error as APIError {
            XCTAssertEqual(error, APIError(code: -1, errorBody: "Not signed in"))
        } catch {
            XCTFail("unexpected error \(error)")
        }
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "no request may leave without a user token")
    }

    func testRefreshDiscardsResponseWhenUserLogsOutMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: "[\(typeJSON())]")
        StubURLProtocol.onRequest = { _ in identity.clearUserSession() }

        try await repo.refreshCustomTypes()

        XCTAssertTrue(
            repo.currentTypes.isEmpty,
            "a response landing after logout must not silently repopulate the cleared cache"
        )
    }

    func testRefreshDiscardsResponseWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: "[\(typeJSON())]")
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        try await repo.refreshCustomTypes()

        XCTAssertTrue(
            repo.currentTypes.isEmpty,
            "the previous account's list must never leak into the next account's cache"
        )
    }

    func testRefreshAppliesWhenTokenRotatesForSameUserMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: "[\(typeJSON())]")
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "rotated-token",
                email: "user@example.com",
                userId: "u1",
                role: "user"
            )
        }

        try await repo.refreshCustomTypes()

        XCTAssertEqual(
            repo.currentTypes.map(\.id), ["ab12cd34ef56"],
            "the guard keys on the user id, not the token — a token refresh mid-flight must not drop data"
        )
    }

    // MARK: - Mutations

    func testCreatePostsAndUpsertsWithoutRelisting() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 201, body: typeJSON())
        let created = try await repo.createType(name: "Aider", command: "aider --model sonnet")

        XCTAssertEqual(created.id, "ab12cd34ef56")
        XCTAssertEqual(repo.currentTypes.map(\.id), ["ab12cd34ef56"])
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "the returned row is applied — no follow-up list fetch")
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "POST")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["name": "Aider", "command": "aider --model sonnet"])
    }

    func testUpdatePatchesBothFieldsAndAppliesRow() async throws {
        let repo = makeRepo()
        await repo.applyUpserted(makeCustomAgentType())
        StubURLProtocol.enqueue(status: 200, body: typeJSON(name: "Aider 2", command: "aider --pro", updatedAt: "u2"))

        let updated = try await repo.updateType(typeId: "ab12cd34ef56", name: "Aider 2", command: "aider --pro")

        XCTAssertEqual(updated.name, "Aider 2")
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "PATCH")
        XCTAssertEqual(StubURLProtocol.requests.first?.url?.path, "/api/custom-agent-types/ab12cd34ef56")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(
            body, ["name": "Aider 2", "command": "aider --pro"],
            "both fields always go on the wire — the server 422s an empty PATCH"
        )
        XCTAssertEqual(repo.currentTypes.map(\.name), ["Aider 2"], "the bumped row replaces in place")
    }

    func testCreateDiscardsCacheWriteWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 201, body: typeJSON())
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        let created = try await repo.createType(name: "Aider", command: "aider --model sonnet")

        XCTAssertEqual(created.id, "ab12cd34ef56", "the server row is still returned on mismatch")
        XCTAssertTrue(
            repo.currentTypes.isEmpty,
            "a create landing after an account switch must not write the previous account's row into the cache"
        )
    }

    // MARK: - Push-driven cache mutations

    func testApplyUpsertedAppendsThenReplacesInPlacePreservingOrder() async {
        let repo = makeRepo()
        await repo.applyUpserted(makeCustomAgentType(id: "aaaaaaaaaaaa", name: "First"))
        await repo.applyUpserted(makeCustomAgentType(id: "ab12cd34ef56", name: "Aider"))
        await repo.applyUpserted(makeCustomAgentType(id: "ffffffffffff", name: "Goose"))

        await repo.applyUpserted(makeCustomAgentType(id: "ab12cd34ef56", name: "Aider 2", updatedAt: "u2"))

        XCTAssertEqual(repo.currentTypes.map(\.id), ["aaaaaaaaaaaa", "ab12cd34ef56", "ffffffffffff"])
        XCTAssertEqual(repo.currentTypes[1].name, "Aider 2")
    }
}
