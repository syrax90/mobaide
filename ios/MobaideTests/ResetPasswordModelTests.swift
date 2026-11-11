import XCTest
@testable import Mobaide

@MainActor
final class ResetPasswordModelTests: XCTestCase {
    private var harness: RepositoryHarness!
    private var sleeper: RecordingSleeper!

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        harness = RepositoryHarness()
        sleeper = RecordingSleeper()
    }

    private func makeModel() -> ResetPasswordModel {
        ResetPasswordModel(
            email: "user@example.com",
            authRepository: harness.repository,
            sleeper: sleeper
        )
    }

    private func fillValidForm(_ model: ResetPasswordModel) {
        model.code = "123456"
        model.newPassword = "new-password-1"
        model.confirmPassword = "new-password-1"
    }

    func testSuccessEmitsSuccessEvent() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"status":"ok"}"#)
        let model = makeModel()
        let events = StreamCollector(model.successEvents())
        fillValidForm(model)

        await model.submit()

        _ = try await events.next()
        XCTAssertNil(model.error)
    }

    func testInvalidCodeClearsCodeField() async {
        StubURLProtocol.enqueue(status: 400, body: #"{"detail":{"error":"reset_code_invalid"}}"#)
        let model = makeModel()
        fillValidForm(model)

        await model.submit()

        XCTAssertEqual(model.error, "That code isn't right. Try again.")
        XCTAssertEqual(model.code, "")
    }

    func testSameAsOldPasswordIsRejected() async {
        StubURLProtocol.enqueue(status: 400, body: #"{"detail":{"error":"new_password_same_as_old"}}"#)
        let model = makeModel()
        fillValidForm(model)

        await model.submit()

        XCTAssertEqual(model.error, "New password must differ from your current password.")
    }

    func testIpRateLimitOnSubmitShowsRetryAfterMessage() async {
        StubURLProtocol.enqueue(status: 429, body: """
        {"detail":{"error":"reset_ip_rate_limited","retry_after_seconds":9}}
        """)
        let model = makeModel()
        fillValidForm(model)

        await model.submit()

        XCTAssertEqual(model.error, "Too many requests from this network. Try again in 9s.")
    }

    func testResendStartsCooldown() async throws {
        StubURLProtocol.enqueue(status: 200, body: #"{"status":"ok"}"#)
        let model = makeModel()
        let messages = StreamCollector(model.messages())

        await model.resend()
        await model.cooldownTask?.value

        let message = try await messages.next()
        XCTAssertEqual(message, "A new code is on its way.")
        XCTAssertEqual(sleeper.durations, Array(repeating: .seconds(1), count: 60))
        XCTAssertEqual(model.cooldownSeconds, 0)
    }
}
