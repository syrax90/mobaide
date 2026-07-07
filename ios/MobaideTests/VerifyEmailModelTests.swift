import XCTest
@testable import Mobaide

@MainActor
final class VerifyEmailModelTests: XCTestCase {
    private var harness: RepositoryHarness!
    private var sleeper: RecordingSleeper!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        harness = RepositoryHarness()
        sleeper = RecordingSleeper()
    }

    private func makeModel(email: String = "user@example.com") -> VerifyEmailModel {
        VerifyEmailModel(email: email, authRepository: harness.repository, sleeper: sleeper)
    }

    func testShortCodeIsGatedWithoutARequest() async {
        let model = makeModel()
        model.code = "123"

        await model.submit()

        XCTAssertEqual(model.error, "Enter the 6-digit code we emailed you")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testSuccessEmitsEventAndPersistsSession() async throws {
        StubURLProtocol.enqueue(status: 200, body: """
        {"access_token":"jwt-v","user_id":"u1","email":"user@example.com","role":"user"}
        """)
        let model = makeModel()
        let events = StreamCollector(model.successEvents())
        model.code = "123456"

        await model.submit()

        _ = try await events.next()
        XCTAssertEqual(harness.session.userIdentityStore.getUserAccessToken(), "jwt-v")
        let state = await harness.session.sessionManager.currentAuthState
        XCTAssertEqual(state, .userOnly)
    }

    func testWrongCode400ClearsCodeWithFriendlyMessage() async {
        StubURLProtocol.enqueue(status: 400, body: #"{"detail":{"error":"verification_code_invalid"}}"#)
        let model = makeModel()
        model.code = "000000"

        await model.submit()

        XCTAssertEqual(model.error, "That code isn't right. Try again.")
        XCTAssertEqual(model.code, "")
    }

    func testExpiredCode410MapsToExpiredMessage() async {
        StubURLProtocol.enqueue(status: 410, body: #"{"detail":{"error":"verification_code_expired"}}"#)
        let model = makeModel()
        model.code = "123456"

        await model.submit()

        XCTAssertEqual(model.error, "That code expired. Tap \"Resend\" to get a new one.")
    }

    func testAttemptsExhausted429MapsToTooManyAttempts() async {
        StubURLProtocol.enqueue(status: 429, body: #"{"detail":{"error":"verification_attempts_exhausted"}}"#)
        let model = makeModel()
        model.code = "123456"

        await model.submit()

        XCTAssertEqual(model.error, "Too many attempts. Request a new code.")
    }

    func testResendStartsSixtySecondCooldownCountingEachSecond() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"status":"sent"}"#)
        let model = makeModel()
        let messages = StreamCollector(model.messages())

        await model.resend()
        await model.cooldownTask?.value

        let message = try await messages.next()
        XCTAssertEqual(message, "A new code is on its way.")
        XCTAssertEqual(sleeper.durations, Array(repeating: .seconds(1), count: 60))
        XCTAssertEqual(model.cooldownSeconds, 0)
    }

    func testServerRateLimitSeedsCooldownFromResponse() async throws {
        StubURLProtocol.enqueue(status: 429, body: """
        {"detail":{"error":"resend_rate_limited","cooldown_seconds_remaining":5}}
        """)
        let model = makeModel()
        let messages = StreamCollector(model.messages())

        await model.resend()
        await model.cooldownTask?.value

        let message = try await messages.next()
        XCTAssertEqual(message, "Please wait 5s before requesting another code.")
        XCTAssertEqual(sleeper.durations, Array(repeating: .seconds(1), count: 5))
        XCTAssertEqual(model.cooldownSeconds, 0)
    }

    func testResendIsGatedWhileCoolingDown() async {
        StubURLProtocol.enqueue(status: 200, body: #"{"status":"sent"}"#)
        // Real sleeper: the 60s countdown is still at ~60 when the second
        // resend fires, so the guard must swallow it.
        let model = VerifyEmailModel(email: "user@example.com", authRepository: harness.repository)

        await model.resend()
        XCTAssertGreaterThan(model.cooldownSeconds, 0)
        await model.resend()

        XCTAssertEqual(StubURLProtocol.requests.count, 1)
        model.cooldownTask?.cancel()
    }
}
