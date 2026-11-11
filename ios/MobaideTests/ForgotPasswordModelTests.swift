import XCTest
@testable import Mobaide

@MainActor
final class ForgotPasswordModelTests: XCTestCase {
    private var harness: RepositoryHarness!
    private var sleeper: RecordingSleeper!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        harness = RepositoryHarness()
        sleeper = RecordingSleeper()
    }

    private func makeModel() -> ForgotPasswordModel {
        ForgotPasswordModel(authRepository: harness.repository, sleeper: sleeper)
    }

    func testSentShowsEnumerationResistantMessageAndNavigates() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"status":"ok"}"#)
        let model = makeModel()
        let navEvents = StreamCollector(model.codeSentEvents())
        let messages = StreamCollector(model.messages())
        model.email = "  user@example.com  "

        await model.submit()

        let message = try await messages.next()
        XCTAssertEqual(message, ForgotPasswordModel.genericSentMessage)
        let email = try await navEvents.next()
        XCTAssertEqual(email, "user@example.com", "email is trimmed before submission")
    }

    func testIpRateLimitSeedsCooldownFromRetryAfter() async throws {
        StubURLProtocol.enqueue(status: 429, body: """
        {"detail":{"error":"reset_ip_rate_limited","retry_after_seconds":7}}
        """)
        let model = makeModel()
        let messages = StreamCollector(model.messages())
        model.email = "user@example.com"

        await model.submit()
        await model.cooldownTask?.value

        let message = try await messages.next()
        XCTAssertEqual(message, "Too many requests from this network. Try again in 7s.")
        XCTAssertEqual(sleeper.durations, Array(repeating: .seconds(1), count: 7))
        XCTAssertEqual(model.cooldownSeconds, 0)
    }

    func testSubmitIsGatedWhileCoolingDown() async {
        StubURLProtocol.enqueue(status: 429, body: """
        {"detail":{"error":"reset_ip_rate_limited","retry_after_seconds":60}}
        """)
        // Real sleeper: cooldown is still counting when the second submit fires.
        let model = ForgotPasswordModel(authRepository: harness.repository)
        model.email = "user@example.com"

        await model.submit()
        XCTAssertGreaterThan(model.cooldownSeconds, 0)
        await model.submit()

        XCTAssertEqual(StubURLProtocol.requests.count, 1)
        model.cooldownTask?.cancel()
    }
}
