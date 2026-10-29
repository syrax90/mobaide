import XCTest
@testable import Mobaide

@MainActor
final class TemplatesRepositoryTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeRepo() -> TemplatesRepository {
        AgentHarness().templatesRepository
    }

    /// Repo plus its (Sendable) identity store, for tests that flip the
    /// signed-in account while a request is in flight.
    private func makeRepoWithIdentity() -> (repo: TemplatesRepository, identity: UserIdentityStore) {
        let session = SessionHarness(userLoggedIn: true)
        return (AgentHarness(session: session).templatesRepository, session.userIdentityStore)
    }

    private static func switchAccountOnRequest(_ identity: UserIdentityStore) {
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }
    }

    private func templateJSON(id: String, name: String, favorite: Bool, version: Int = 1) -> String {
        #"""
        {"template_id":"\#(id)","name":"\#(name)","body":"b","is_favorite":\#(favorite),"created_at":"t","updated_at":"t","version":\#(version)}
        """#
    }

    func testRefreshReplacesCache() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: "[\(templateJSON(id: "t1", name: "A", favorite: false))]")
        try await repo.refreshTemplates()
        XCTAssertEqual(repo.currentTemplates.map(\.template_id), ["t1"])
    }

    func testApplyUpsertedInsertsThenReplacesById() async {
        let repo = makeRepo()
        await repo.applyUpserted(makeTemplate(id: "t1", name: "One", favorite: false))
        XCTAssertEqual(repo.currentTemplates.count, 1)

        await repo.applyUpserted(makeTemplate(id: "t1", name: "One-edited", favorite: true, version: 2))
        XCTAssertEqual(repo.currentTemplates.count, 1)
        XCTAssertEqual(repo.currentTemplates.first?.name, "One-edited")
        XCTAssertEqual(repo.currentTemplates.first?.is_favorite, true)
    }

    func testApplyDeletedRemovesById() async {
        let repo = makeRepo()
        await repo.applyUpserted(makeTemplate(id: "t1", name: "One", favorite: false))
        await repo.applyUpserted(makeTemplate(id: "t2", name: "Two", favorite: false))
        await repo.applyDeleted(templateId: "t1")
        XCTAssertEqual(repo.currentTemplates.map(\.template_id), ["t2"])
    }

    func testCreateAppliesServerReturnedRow() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: templateJSON(id: "t1", name: "Made", favorite: false))
        let created = try await repo.createTemplate(name: "Made", body: "b")
        XCTAssertEqual(created.template_id, "t1")
        XCTAssertEqual(repo.currentTemplates.map(\.template_id), ["t1"])
    }

    func testUpdateSendsPatchAndAppliesResult() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: templateJSON(id: "t1", name: "A", favorite: true, version: 2))
        _ = try await repo.updateTemplate(templateId: "t1", update: UpdateTemplateRequest(is_favorite: true))
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "PATCH")
        XCTAssertEqual(repo.currentTemplates.first?.is_favorite, true)
    }

    func testDeleteRemovesFromCache() async throws {
        let repo = makeRepo()
        await repo.applyUpserted(makeTemplate(id: "t1", name: "One", favorite: false))
        StubURLProtocol.enqueue(status: 204, body: "")
        try await repo.deleteTemplate(templateId: "t1")
        XCTAssertTrue(repo.currentTemplates.isEmpty)
    }

    func testClearEmptiesCache() async {
        let repo = makeRepo()
        await repo.applyUpserted(makeTemplate(id: "t1", name: "One", favorite: false))
        XCTAssertFalse(repo.currentTemplates.isEmpty)
        await repo.clear()
        XCTAssertTrue(repo.currentTemplates.isEmpty)
    }

    func testRefreshDropsResponseWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: "[\(templateJSON(id: "t1", name: "A", favorite: false))]")
        Self.switchAccountOnRequest(identity)

        try await repo.refreshTemplates()

        XCTAssertTrue(
            repo.currentTemplates.isEmpty,
            "a list landing after an account switch must not show the previous account's templates"
        )
    }

    func testRefreshAppliesResponseWhenTokenRotatesForSameUserMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: "[\(templateJSON(id: "t1", name: "A", favorite: false))]")
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "rotated-token",
                email: "user@example.com",
                userId: "u1",
                role: "user"
            )
        }

        try await repo.refreshTemplates()

        XCTAssertEqual(repo.currentTemplates.map(\.template_id), ["t1"], "the guard keys on user id, not on the token")
    }

    func testCreateDiscardsCacheWriteWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: templateJSON(id: "t1", name: "Made", favorite: false))
        Self.switchAccountOnRequest(identity)

        let created = try await repo.createTemplate(name: "Made", body: "b")

        XCTAssertEqual(created.template_id, "t1", "the server row is still returned on mismatch")
        XCTAssertTrue(repo.currentTemplates.isEmpty)
    }

    func testUpdateDiscardsCacheWriteWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        await repo.applyUpserted(makeTemplate(id: "t1", name: "A", favorite: false))
        StubURLProtocol.enqueue(status: 200, body: templateJSON(id: "t1", name: "A", favorite: true, version: 2))
        Self.switchAccountOnRequest(identity)

        let updated = try await repo.updateTemplate(templateId: "t1", update: UpdateTemplateRequest(is_favorite: true))

        XCTAssertEqual(updated.is_favorite, true, "the server row is still returned on mismatch")
        XCTAssertEqual(repo.currentTemplates.first?.is_favorite, false)
        XCTAssertEqual(repo.currentTemplates.first?.version, 1)
    }

    func testDeleteDiscardsCacheWriteWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        await repo.applyUpserted(makeTemplate(id: "t1", name: "One", favorite: false))
        StubURLProtocol.enqueue(status: 204, body: "")
        Self.switchAccountOnRequest(identity)

        try await repo.deleteTemplate(templateId: "t1")

        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "DELETE", "the server delete itself still runs")
        XCTAssertEqual(repo.currentTemplates.map(\.template_id), ["t1"])
    }
}
