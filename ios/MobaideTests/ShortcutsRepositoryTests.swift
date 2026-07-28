import XCTest
@testable import Mobaide

/// `ShortcutsRepository` — the saved-shortcut sibling of `TemplatesRepository`:
/// REST delegation over `StubURLProtocol`, the user-token requirement, and the
/// cache mutations driven both by REST round-trips and by `applyUpserted` /
/// `applyDeleted` (the entrypoints `UserEventBus` calls for `shortcut.*`
/// pushes). Mirrors Android `ShortcutsRepositoryTest`.
@MainActor
final class ShortcutsRepositoryTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeRepo(loggedIn: Bool = true) -> ShortcutsRepository {
        AgentHarness(session: SessionHarness(userLoggedIn: loggedIn)).shortcutsRepository
    }

    /// Repo plus its (Sendable) identity store, for tests that flip the
    /// signed-in account while a request is in flight.
    private func makeRepoWithIdentity() -> (repo: ShortcutsRepository, identity: UserIdentityStore) {
        let session = SessionHarness(userLoggedIn: true)
        return (AgentHarness(session: session).shortcutsRepository, session.userIdentityStore)
    }

    private func shortcutJSON(
        id: String,
        kind: String = "claude",
        chordToken: String,
        name: String? = nil,
        position: Int = 0,
        version: Int = 1
    ) -> String {
        let namePart = name.map { #","name":"\#($0)""# } ?? ""
        return #"{"shortcut_id":"\#(id)","kind":"\#(kind)","chord_token":"\#(chordToken)"\#(namePart),"position":\#(position),"created_at":"c","updated_at":"u","version":\#(version)}"#
    }

    func testInitialCacheIsEmpty() {
        XCTAssertTrue(makeRepo().currentShortcuts.isEmpty)
    }

    func testRefreshReplacesCacheWholesale() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(
            status: 200,
            body: "[\(shortcutJSON(id: "s-1", chordToken: "ctrl+a")),\(shortcutJSON(id: "s-2", chordToken: "ctrl+b"))]"
        )
        try await repo.refreshShortcuts()
        XCTAssertEqual(repo.currentShortcuts.map(\.shortcut_id), ["s-1", "s-2"])

        StubURLProtocol.enqueue(status: 200, body: "[\(shortcutJSON(id: "s-3", chordToken: "ctrl+c"))]")
        try await repo.refreshShortcuts()
        XCTAssertEqual(
            repo.currentShortcuts.map(\.shortcut_id), ["s-3"],
            "a refresh replaces the whole snapshot, never merges"
        )
    }

    func testRefreshWhileLoggedOutThrowsWithoutNetwork() async {
        let repo = makeRepo(loggedIn: false)
        do {
            try await repo.refreshShortcuts()
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
        StubURLProtocol.enqueue(status: 200, body: "[\(shortcutJSON(id: "s-1", chordToken: "ctrl+a"))]")
        StubURLProtocol.onRequest = { _ in identity.clearUserSession() }

        try await repo.refreshShortcuts()

        XCTAssertTrue(
            repo.currentShortcuts.isEmpty,
            "a response landing after logout must not silently repopulate the cleared cache"
        )
    }

    func testRefreshDiscardsResponseWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: "[\(shortcutJSON(id: "s-old-account", chordToken: "ctrl+a"))]")
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        try await repo.refreshShortcuts()

        XCTAssertTrue(
            repo.currentShortcuts.isEmpty,
            "the previous account's list must never leak into the next account's cache"
        )
    }

    func testRefreshAppliesWhenTokenRotatesForSameUserMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: "[\(shortcutJSON(id: "s-1", chordToken: "ctrl+a"))]")
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "rotated-token",
                email: "user@example.com",
                userId: "u1",
                role: "user"
            )
        }

        try await repo.refreshShortcuts()

        XCTAssertEqual(
            repo.currentShortcuts.map(\.shortcut_id), ["s-1"],
            "the guard keys on the user id, not the token — a token refresh mid-flight must not drop data"
        )
    }

    func testCreatePostsAndUpsertsWithoutRelisting() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 201, body: shortcutJSON(id: "s-new", chordToken: "ctrl+n"))
        let created = try await repo.createShortcut(kind: "claude", chordToken: "ctrl+n")

        XCTAssertEqual(created.shortcut_id, "s-new")
        XCTAssertEqual(repo.currentShortcuts.map(\.shortcut_id), ["s-new"])
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "the returned row is applied — no follow-up list fetch")
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "POST")
    }

    func testCreateUpsertReturningExistingRowReplacesInPlace() async throws {
        // POST /api/shortcuts is a server-side upsert: re-saving a live
        // (kind, chord) returns the already-cached row with a new name/version.
        let repo = makeRepo()
        StubURLProtocol.enqueue(
            status: 200,
            body: "[\(shortcutJSON(id: "s-1", chordToken: "ctrl+x", name: "Old")),\(shortcutJSON(id: "s-2", chordToken: "ctrl+y"))]"
        )
        try await repo.refreshShortcuts()

        StubURLProtocol.enqueue(status: 201, body: shortcutJSON(id: "s-1", chordToken: "ctrl+x", name: "New", version: 2))
        _ = try await repo.createShortcut(kind: "claude", chordToken: "ctrl+x", name: "New")

        XCTAssertEqual(
            repo.currentShortcuts.map(\.shortcut_id), ["s-1", "s-2"],
            "an upsert must replace in place, never grow a duplicate button"
        )
        XCTAssertEqual(repo.currentShortcuts.first?.name, "New")
        XCTAssertEqual(repo.currentShortcuts.first?.version, 2)
    }

    func testRenamePatchesAndAppliesBumpedRow() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: "[\(shortcutJSON(id: "s-1", chordToken: "ctrl+x", name: "Old"))]")
        try await repo.refreshShortcuts()

        StubURLProtocol.enqueue(status: 200, body: shortcutJSON(id: "s-1", chordToken: "ctrl+x", name: "New", version: 2))
        let returned = try await repo.renameShortcut(shortcutId: "s-1", name: "New")

        XCTAssertEqual(returned.name, "New")
        XCTAssertEqual(StubURLProtocol.requests.last?.httpMethod, "PATCH")
        XCTAssertEqual(repo.currentShortcuts.first?.name, "New")
        XCTAssertEqual(repo.currentShortcuts.first?.version, 2)
    }

    func testRenameEmptyNameSendsClearSentinelVerbatim() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: shortcutJSON(id: "s-1", chordToken: "ctrl+x", version: 2))
        _ = try await repo.renameShortcut(shortcutId: "s-1", name: "")

        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["name": ""], #"clearing sends {"name":""} — the server 422s on {}"#)
    }

    func testDeleteRemovesFromCache() async throws {
        let repo = makeRepo()
        await repo.applyUpserted(makeShortcut(id: "s-1", chordToken: "ctrl+a"))
        await repo.applyUpserted(makeShortcut(id: "s-2", chordToken: "ctrl+b"))

        StubURLProtocol.enqueue(status: 204, body: "")
        try await repo.deleteShortcut(shortcutId: "s-1")

        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "DELETE")
        XCTAssertEqual(repo.currentShortcuts.map(\.shortcut_id), ["s-2"])
    }

    func testCreateDiscardsCacheWriteWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 201, body: shortcutJSON(id: "s-new", chordToken: "ctrl+n"))
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        let created = try await repo.createShortcut(kind: "claude", chordToken: "ctrl+n")

        XCTAssertEqual(created.shortcut_id, "s-new", "the server row is still returned on mismatch")
        XCTAssertTrue(
            repo.currentShortcuts.isEmpty,
            "a create landing after an account switch must not write the previous account's row into the cache"
        )
    }

    func testRenameDiscardsCacheWriteWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        await repo.applyUpserted(makeShortcut(id: "s-1", chordToken: "ctrl+x", name: "Old"))
        StubURLProtocol.enqueue(status: 200, body: shortcutJSON(id: "s-1", chordToken: "ctrl+x", name: "New", version: 2))
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        let returned = try await repo.renameShortcut(shortcutId: "s-1", name: "New")

        XCTAssertEqual(returned.name, "New", "the server row is still returned on mismatch")
        XCTAssertEqual(repo.currentShortcuts.first?.name, "Old", "the bumped row must not reach the cache")
        XCTAssertEqual(repo.currentShortcuts.first?.version, 1)
    }

    func testDeleteDiscardsCacheWriteWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        await repo.applyUpserted(makeShortcut(id: "s-1", chordToken: "ctrl+a"))
        StubURLProtocol.enqueue(status: 204, body: "")
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        try await repo.deleteShortcut(shortcutId: "s-1")

        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "DELETE", "the server delete itself still runs")
        XCTAssertEqual(
            repo.currentShortcuts.map(\.shortcut_id), ["s-1"],
            "a delete landing after an account switch must not mutate the cache it no longer owns"
        )
    }

    func testCreateAppliesCacheWriteWhenTokenRotatesForSameUserMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 201, body: shortcutJSON(id: "s-new", chordToken: "ctrl+n"))
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "rotated-token",
                email: "user@example.com",
                userId: "u1",
                role: "user"
            )
        }

        _ = try await repo.createShortcut(kind: "claude", chordToken: "ctrl+n")

        XCTAssertEqual(
            repo.currentShortcuts.map(\.shortcut_id), ["s-new"],
            "the guard keys on the user id, not the token — a token refresh mid-flight must not drop the write"
        )
    }

    func testApplyUpsertedAppendsThenReplacesInPlacePreservingOrder() async {
        let repo = makeRepo()
        await repo.applyUpserted(makeShortcut(id: "s-1", chordToken: "ctrl+a"))
        await repo.applyUpserted(makeShortcut(id: "s-2", chordToken: "ctrl+b", name: "Old"))
        await repo.applyUpserted(makeShortcut(id: "s-3", chordToken: "ctrl+c"))

        await repo.applyUpserted(makeShortcut(id: "s-2", chordToken: "ctrl+b", name: "New", version: 7))

        XCTAssertEqual(repo.currentShortcuts.map(\.shortcut_id), ["s-1", "s-2", "s-3"])
        XCTAssertEqual(repo.currentShortcuts[1].name, "New")
        XCTAssertEqual(repo.currentShortcuts[1].version, 7)
    }

    func testApplyDeletedRemovesMatchingAndIgnoresUnknownId() async {
        let repo = makeRepo()
        await repo.applyUpserted(makeShortcut(id: "s-1", chordToken: "ctrl+a"))
        await repo.applyUpserted(makeShortcut(id: "s-2", chordToken: "ctrl+b"))

        await repo.applyDeleted(shortcutId: "s-1")
        XCTAssertEqual(repo.currentShortcuts.map(\.shortcut_id), ["s-2"])

        await repo.applyDeleted(shortcutId: "ghost-id")
        XCTAssertEqual(
            repo.currentShortcuts.map(\.shortcut_id), ["s-2"],
            "a tombstone for a never-fetched row must be a no-op"
        )
    }

    func testPurgeKindRemovesOnlyTheExactWireKind() async {
        // Client half of the server-side cascade on `custom_type.deleted`,
        // which soft-deletes the type's shortcuts without per-shortcut events.
        let repo = makeRepo()
        await repo.applyUpserted(makeShortcut(id: "s-1", kind: "custom_ab12cd34ef56", chordToken: "ctrl+a"))
        await repo.applyUpserted(makeShortcut(id: "s-2", kind: "custom_ffffffffffff", chordToken: "ctrl+b"))
        await repo.applyUpserted(makeShortcut(id: "s-3", kind: "claude", chordToken: "ctrl+c"))

        await repo.purgeKind("ab12cd34ef56")
        XCTAssertEqual(
            repo.currentShortcuts.map(\.shortcut_id), ["s-1", "s-2", "s-3"],
            "the purge matches the full wire kind — a bare type id must be a no-op"
        )

        await repo.purgeKind("custom_ab12cd34ef56")
        XCTAssertEqual(
            repo.currentShortcuts.map(\.shortcut_id), ["s-2", "s-3"],
            "only the deleted type's bucket is purged — sibling custom types and built-ins survive"
        )
    }

    func testClearEmptiesCache() async {
        let repo = makeRepo()
        await repo.applyUpserted(makeShortcut(id: "s-1", chordToken: "ctrl+a"))
        XCTAssertFalse(repo.currentShortcuts.isEmpty)

        await repo.clear()
        XCTAssertTrue(repo.currentShortcuts.isEmpty)
    }
}
