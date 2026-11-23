import XCTest
@testable import Mobaide

/// `CloudVoiceRepository`: "not offered" for an older server and for no
/// account, the engine choice shown at once and taken back on a refusal, and
/// which answer wins when a refresh, a save and an account change cross.
@MainActor
final class CloudVoiceRepositoryTests: XCTestCase {
    private let agent = "a1b2c3d4e5f6"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeRepo(userLoggedIn: Bool = true) -> (repo: CloudVoiceRepository, identity: UserIdentityStore) {
        let session = SessionHarness(userLoggedIn: userLoggedIn)
        let harness = AgentHarness(session: session)
        let repo = CloudVoiceRepository(centralAPI: harness.centralAPI, userIdentityStore: session.userIdentityStore)
        return (repo, session.userIdentityStore)
    }

    private func status(_ state: String = "ready", engines: [String: String] = [:], used: Int = 0) -> CloudVoiceStatus {
        CloudVoiceStatus(
            state: state,
            limits: CloudVoiceLimits(sample_rate: 16_000, max_seconds: 120, max_chunk_bytes: 65_536),
            usage: CloudVoiceUsage(used_seconds: used, limit_seconds: 1800, resets_at: "2026-11-23T00:00:00Z"),
            engines: engines
        )
    }

    private func enqueue(_ status: CloudVoiceStatus) throws {
        let body = String(decoding: try WireJSON.encoder().encode(status), as: UTF8.self)
        StubURLProtocol.enqueue(status: 200, body: body)
    }

    private nonisolated static func switchAccount(_ identity: UserIdentityStore) {
        identity.saveUserSession(
            accessToken: "user-token-2",
            email: "other@example.com",
            userId: "u2",
            role: "user"
        )
    }

    // MARK: - Status

    func testRefreshShowsTheServerStatus() async throws {
        let repo = makeRepo().repo
        let answer = status(engines: [agent: "cloud"], used: 742)
        try enqueue(answer)

        try await repo.refreshCloudVoice()

        XCTAssertEqual(repo.status, answer)
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "GET")
        XCTAssertEqual(request.url?.path, "/api/voice/cloud")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer user-token")
    }

    func testAServerWithoutTheRouteReadsAsNotOffered() async throws {
        let repo = makeRepo().repo
        try enqueue(status())
        try await repo.refreshCloudVoice()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Not Found"}"#)

        try await repo.refreshCloudVoice()

        XCTAssertNil(repo.status)
    }

    func testWithoutAnAccountNothingIsOfferedAndNoServerIsAsked() async throws {
        let repo = makeRepo(userLoggedIn: false).repo

        try await repo.refreshCloudVoice()

        XCTAssertNil(repo.status)
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testAFailedRefreshKeepsTheLastStatusAndThrows() async throws {
        let repo = makeRepo().repo
        let ready = status()
        try enqueue(ready)
        try await repo.refreshCloudVoice()
        StubURLProtocol.enqueue(status: 502, body: "")

        do {
            try await repo.refreshCloudVoice()
            XCTFail("expected the refresh to throw")
        } catch {}

        XCTAssertEqual(repo.status, ready)
    }

    func testRefreshDiscardsTheAnswerWhenTheAccountSwitchesMidFlight() async throws {
        let (repo, identity) = makeRepo()
        try enqueue(status(engines: [agent: "cloud"]))
        StubURLProtocol.onRequest = { _ in Self.switchAccount(identity) }

        try await repo.refreshCloudVoice()

        XCTAssertNil(repo.status, "the previous account's status must never reach the next account")
    }

    func testClearDropsTheStatusAndAnAnswerStillInFlight() async throws {
        let repo = makeRepo().repo
        try enqueue(status())
        try await repo.refreshCloudVoice()
        try enqueue(status(engines: [agent: "cloud"]))
        StubURLProtocol.hold = { _ in true }

        let refreshing = Task { try await repo.refreshCloudVoice() }
        await waitUntil { StubURLProtocol.requests.count == 2 }
        await repo.clear()
        XCTAssertNil(repo.status)
        StubURLProtocol.releaseHeld()
        try await refreshing.value

        XCTAssertNil(repo.status)
    }

    func testEnginesChangedShowsTheChoicesAtOnceAndReadsTheStatusAgain() async throws {
        let repo = makeRepo().repo
        try enqueue(status())
        try await repo.refreshCloudVoice()
        let reread = status("unavailable", engines: [agent: "cloud"])
        try enqueue(reread)

        await repo.applyEnginesChanged(CloudVoiceChangedPayload(engines: [agent: "cloud"]))

        XCTAssertEqual(repo.status, status(engines: [agent: "cloud"]))
        await waitUntil { repo.status == reread }
        XCTAssertEqual(repo.status, reread)
    }

    // MARK: - Engine choice

    func testSetEnginePutsTheChoiceAndKeepsTheServerAnswer() async throws {
        let repo = makeRepo().repo
        let saved = status(engines: [agent: "cloud"], used: 61)
        try enqueue(saved)

        try await repo.setEngine(.cloud, agentId: agent)

        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "PUT")
        XCTAssertEqual(request.url?.path, "/api/voice/cloud/engines/\(agent)")
        let body = try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(body, ["engine": "cloud"])
        XCTAssertEqual(repo.status, saved)
    }

    func testSetEngineShowsTheChoiceAtOnceAndTakesItBackWhenRefused() async throws {
        let repo = makeRepo().repo
        let before = status(engines: [agent: "local"])
        try enqueue(before)
        try await repo.refreshCloudVoice()
        StubURLProtocol.enqueue(
            status: 403,
            body: #"{"detail":{"code":"cloud_disabled","message":"Cloud voice input is not available."}}"#
        )
        StubURLProtocol.hold = { $0.httpMethod == "PUT" }

        let saving = Task { try await repo.setEngine(.cloud, agentId: agent) }
        await waitUntil { StubURLProtocol.requests.count == 2 }
        XCTAssertEqual(repo.status?.engine(agentId: agent), .cloud, "the choice shows before the server answers")
        StubURLProtocol.releaseHeld()

        do {
            try await saving.value
            XCTFail("expected the save to throw")
        } catch let error as VoiceApiError {
            XCTAssertEqual(error.status, 403)
            XCTAssertEqual(error.code, "cloud_disabled")
        }
        XCTAssertEqual(repo.status, before)
    }

    func testAChoiceBeingSavedStaysOnTopOfARefreshThatStillSaysTheOldOne() async throws {
        let repo = makeRepo().repo
        let old = status(engines: [agent: "local"])
        let saved = status(engines: [agent: "cloud"])
        try enqueue(old)
        try await repo.refreshCloudVoice()
        try enqueue(saved)
        try enqueue(old)
        StubURLProtocol.hold = { $0.httpMethod == "PUT" }
        let saving = Task { try await repo.setEngine(.cloud, agentId: agent) }
        await waitUntil { StubURLProtocol.requests.count == 2 }

        // A push from another device, or a screen, reads the status meanwhile.
        try await repo.refreshCloudVoice()

        XCTAssertEqual(repo.status?.engine(agentId: agent), .cloud)
        StubURLProtocol.releaseHeld()
        try await saving.value
        XCTAssertEqual(repo.status, saved)
    }

    func testARefreshThatStartedBeforeASaveNeverBringsTheOldChoiceBack() async throws {
        let repo = makeRepo().repo
        let saved = status(engines: [agent: "cloud"])
        try enqueue(status(engines: [agent: "local"]))
        try enqueue(saved)
        StubURLProtocol.hold = { $0.httpMethod == "GET" }
        let refreshing = Task { try await repo.refreshCloudVoice() }
        await waitUntil { StubURLProtocol.requests.count == 1 }

        try await repo.setEngine(.cloud, agentId: agent)
        StubURLProtocol.releaseHeld()
        try await refreshing.value

        XCTAssertEqual(repo.status, saved)
    }

    func testAQueuedSaveIsNotSentOnceTheAccountChanges() async throws {
        // The queued PUT would carry the next account's token.
        let (repo, identity) = makeRepo()
        let other = "0f9e8d7c6b5a"
        try enqueue(status())
        try await repo.refreshCloudVoice()
        try enqueue(status(engines: [agent: "cloud"]))
        StubURLProtocol.hold = { $0.httpMethod == "PUT" }

        let first = Task { try await repo.setEngine(.cloud, agentId: agent) }
        await waitUntil { StubURLProtocol.requests.count == 2 }
        let second = Task { try await repo.setEngine(.cloud, agentId: other) }
        await waitUntil { repo.status?.engine(agentId: other) == .cloud }
        Self.switchAccount(identity)
        await repo.clear()
        StubURLProtocol.hold = nil
        StubURLProtocol.releaseHeld()
        try await first.value
        try await second.value

        XCTAssertEqual(StubURLProtocol.requests.map(\.httpMethod), ["GET", "PUT"])
        XCTAssertNil(repo.status, "the first account's answer is not shown to the second")
    }

    // MARK: - Dictation routes

    func testDictationOpensOnTheCentralServerAndRaisesItsCodedRefusals() async throws {
        let api = makeRepo().repo.dictationApi()
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"dictation_id":"d1","sample_rate":16000,"max_seconds":120,"max_chunk_bytes":65536}"#
        )
        StubURLProtocol.enqueue(
            status: 429,
            body: #"{"detail":{"code":"cloud_limit_reached","message":"Used up.","resets_at":"2026-11-23T00:00:00Z"}}"#
        )

        let opened = try await api.create()

        XCTAssertEqual(opened.dictationId, "d1")
        XCTAssertEqual(opened.maxSeconds, 120)
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.path, "/api/voice/cloud/dictations")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer user-token")
        do {
            _ = try await api.create()
            XCTFail("expected the refusal to throw")
        } catch let error as VoiceApiError {
            XCTAssertEqual(error.status, 429)
            XCTAssertEqual(error.code, "cloud_limit_reached")
        }
    }
}
