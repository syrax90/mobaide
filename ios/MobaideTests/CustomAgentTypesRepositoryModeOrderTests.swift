import XCTest
@testable import Mobaide

/// The mode-picker order half of `CustomAgentTypesRepository`: the snapshot
/// from `GET /api/session-mode-order` and the `session_mode_order.updated` push
/// (older versions dropped), the order a save shows while in flight, the
/// revert on failure, serial saves, the account-generation guard, and the
/// reset on sign-out. Mirrors Android `CustomAgentTypesRepositoryModeOrderTest`.
@MainActor
final class CustomAgentTypesRepositoryModeOrderTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeRepoWithIdentity() -> (repo: CustomAgentTypesRepository, identity: UserIdentityStore) {
        let session = SessionHarness(userLoggedIn: true)
        return (AgentHarness(session: session).customAgentTypesRepository, session.userIdentityStore)
    }

    private func makeRepo() -> CustomAgentTypesRepository {
        makeRepoWithIdentity().repo
    }

    private func order(_ version: Int, _ ids: [String]) -> SessionModeOrderDto {
        SessionModeOrderDto(order: ids, version: version, updated_at: "2026-11-04T10:00:00Z")
    }

    private func orderJSON(_ version: Int, _ ids: [String]) -> String {
        let list = ids.map { "\"\($0)\"" }.joined(separator: ",")
        return #"{"order":[\#(list)],"version":\#(version),"updated_at":"2026-11-04T10:00:00Z"}"#
    }

    // MARK: - Snapshot

    func testRefreshShowsTheServerOrder() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: orderJSON(2, ["shell", "claude"]))

        try await repo.refreshModeOrder()

        XCTAssertEqual(repo.currentModeOrder, ["shell", "claude"])
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "GET")
        XCTAssertEqual(StubURLProtocol.requests.first?.url?.path, "/api/session-mode-order")
    }

    func testRefreshDiscardsResponseWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: orderJSON(2, ["shell"]))
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        try await repo.refreshModeOrder()

        XCTAssertEqual(repo.currentModeOrder, [], "the previous account's order must never reach the next account")
    }

    func testApplyDropsAnOlderVersion() async {
        // A push and the GET it raced can land in either order.
        let repo = makeRepo()
        await repo.applyModeOrder(order(3, ["codex"]))

        await repo.applyModeOrder(order(2, ["shell"]))

        XCTAssertEqual(repo.currentModeOrder, ["codex"])
    }

    func testPushThroughTheSyncProtocolReachesTheRepository() async {
        // `UserEventBus` holds the repository as `any CustomTypeSyncApplying`.
        let repo = makeRepo()
        let sync: any CustomTypeSyncApplying = repo

        await sync.applyModeOrder(order(1, ["auto_scan", "claude"]))

        XCTAssertEqual(repo.currentModeOrder, ["auto_scan", "claude"])
    }

    func testReconcileThroughTheSyncProtocolFetchesTheOrder() async throws {
        let repo = makeRepo()
        let sync: any CustomTypeSyncApplying = repo
        StubURLProtocol.enqueue(status: 200, body: orderJSON(4, ["codex"]))

        try await sync.refreshModeOrder()

        XCTAssertEqual(StubURLProtocol.requests.count, 1)
        XCTAssertEqual(repo.currentModeOrder, ["codex"])
    }

    // MARK: - Save

    func testSavePutsTheWholeOrderAndKeepsTheServerAnswer() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: orderJSON(1, ["codex", "claude"]))

        try await repo.saveModeOrder(["codex", "claude"])

        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "PUT")
        XCTAssertEqual(StubURLProtocol.requests.first?.url?.path, "/api/session-mode-order")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["order": ["codex", "claude"]])
        XCTAssertEqual(repo.currentModeOrder, ["codex", "claude"])
    }

    func testSaveShowsTheNewOrderAtOnceAndRevertsOnFailure() async throws {
        let repo = makeRepo()
        await repo.applyModeOrder(order(1, ["shell", "claude"]))
        let shown = StreamCollector(repo.modeOrder())
        let replayed = try await shown.next()
        XCTAssertEqual(replayed, ["shell", "claude"])
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"unavailable"}"#)

        do {
            try await repo.saveModeOrder(["claude", "shell"])
            XCTFail("expected the save to throw")
        } catch {}

        let optimistic = try await shown.next()
        let reverted = try await shown.next()
        XCTAssertEqual(optimistic, ["claude", "shell"], "the drop shows before the server answers")
        XCTAssertEqual(reverted, ["shell", "claude"], "a failed save falls back to the confirmed order")
        XCTAssertEqual(repo.currentModeOrder, ["shell", "claude"])
    }

    func testTwoQuickSavesRunInOrderAndTheFirstAnswerDoesNotFlickerBack() async throws {
        let repo = makeRepo()
        let shown = StreamCollector(repo.modeOrder())
        let replayed = try await shown.next()
        XCTAssertEqual(replayed, [])
        StubURLProtocol.enqueue(status: 200, body: orderJSON(1, ["codex", "claude"]))
        StubURLProtocol.enqueue(status: 200, body: orderJSON(2, ["shell", "codex"]))

        let first = Task { try await repo.saveModeOrder(["codex", "claude"]) }
        await waitUntil { repo.currentModeOrder == ["codex", "claude"] }
        let second = Task { try await repo.saveModeOrder(["shell", "codex"]) }
        try await first.value
        try await second.value

        let bodies = try StubURLProtocol.bodies.map { try WireJSON.decoder().decode(JSONValue.self, from: $0) }
        XCTAssertEqual(bodies, [["order": ["codex", "claude"]], ["order": ["shell", "codex"]]])
        let optimisticFirst = try await shown.next()
        let optimisticSecond = try await shown.next()
        XCTAssertEqual(optimisticFirst, ["codex", "claude"])
        XCTAssertEqual(optimisticSecond, ["shell", "codex"])
        let quiet = await shown.expectNoElement()
        XCTAssertTrue(quiet, "the first save's answer must not briefly show its older order")
        XCTAssertEqual(repo.currentModeOrder, ["shell", "codex"])
    }

    func testSaveDiscardsTheAnswerWhenAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepoWithIdentity()
        StubURLProtocol.enqueue(status: 200, body: orderJSON(4, ["codex"]))
        StubURLProtocol.onRequest = { _ in
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        try await repo.saveModeOrder(["codex"])

        XCTAssertEqual(repo.currentModeOrder, [])
    }

    func testQueuedSaveIsNotSentOnceTheAccountChanges() async throws {
        // The queued PUT would carry the next account's token.
        let (repo, identity) = makeRepoWithIdentity()
        let gate = DispatchSemaphore(value: 0)
        StubURLProtocol.enqueue(status: 200, body: orderJSON(1, ["codex", "claude"]))
        StubURLProtocol.onRequest = { _ in
            gate.wait()
            identity.saveUserSession(
                accessToken: "user-token-2",
                email: "other@example.com",
                userId: "u2",
                role: "user"
            )
        }

        let first = Task { try await repo.saveModeOrder(["codex", "claude"]) }
        await waitUntil { StubURLProtocol.requests.count == 1 }
        let second = Task { try await repo.saveModeOrder(["shell", "codex"]) }
        await waitUntil { repo.currentModeOrder == ["shell", "codex"] }
        gate.signal()
        try await first.value
        try await second.value

        XCTAssertEqual(StubURLProtocol.requests.count, 1)
        XCTAssertEqual(repo.currentModeOrder, [])
    }

    func testClearResetsTheOrderSoTheNextAccountsFirstVersionApplies() async {
        let repo = makeRepo()
        await repo.applyModeOrder(order(5, ["codex"]))

        await repo.clear()
        XCTAssertEqual(repo.currentModeOrder, [])

        await repo.applyModeOrder(order(1, ["shell"]))
        XCTAssertEqual(repo.currentModeOrder, ["shell"])
    }
}
