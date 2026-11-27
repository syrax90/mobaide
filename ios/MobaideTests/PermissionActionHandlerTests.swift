import XCTest
@testable import Mobaide

/// An Allow or Deny from an alert is never dropped silently: the alert goes
/// away once the answer went through, or says why it did not. An outcome
/// never touches an alert that came after it.
@MainActor
final class PermissionActionHandlerTests: XCTestCase {
    private let action = PermissionAction(
        sessionId: "sess-42",
        agentId: nil,
        requestId: "r:3:a1b2c3",
        choice: .allow
    )

    func testAnAnswerThatWentThroughRemovesTheAlert() async {
        let presenter = RecordingPermissionPresenter(shown: ["sess-42": "r:3:a1b2c3"])
        let handler = PermissionActionHandler(send: { _ in }, presenter: presenter)

        await handler.handle(action)

        XCTAssertEqual(presenter.removed, ["sess-42"])
        XCTAssertTrue(presenter.failures.isEmpty)
    }

    func testAnAnswerToAStaleRequestReplacesTheAlertWithTheReason() async {
        let presenter = RecordingPermissionPresenter(shown: ["sess-42": "r:3:a1b2c3"])
        let handler = PermissionActionHandler(
            send: { _ in throw APIError(code: 409, errorBody: "This request was already answered") },
            presenter: presenter
        )

        await handler.handle(action)

        XCTAssertTrue(presenter.removed.isEmpty)
        XCTAssertEqual(presenter.failures.map { $0.action }, [action])
        XCTAssertEqual(presenter.failures.map { $0.body }, ["The request was already answered. Tap to open."])
    }

    func testAnOutcomeLeavesTheAlertOfTheNextRequestAlone() async {
        // The agent raises the next request's alert while it answers this one.
        let outcomes: [(name: String, error: (any Error)?)] = [
            ("answered", nil),
            ("failed", APIError(code: 409, errorBody: "This request was already answered")),
        ]
        for outcome in outcomes {
            let presenter = RecordingPermissionPresenter(shown: ["sess-42": "r:3:a1b2c3"])
            let error = outcome.error
            let handler = PermissionActionHandler(
                send: { action in
                    presenter.show(sessionId: action.sessionId, requestId: "r:4:d4e5f6")
                    if let error { throw error }
                },
                presenter: presenter
            )

            await handler.handle(action)

            XCTAssertTrue(presenter.removed.isEmpty, outcome.name)
            XCTAssertTrue(presenter.failures.isEmpty, outcome.name)
        }
    }

    func testAFailureAfterASignOutPostsNothing() async {
        let presenter = RecordingPermissionPresenter(shown: ["sess-42": "r:3:a1b2c3"])
        let handler = PermissionActionHandler(
            send: { _ in
                presenter.tearDown()
                throw URLError(.notConnectedToInternet)
            },
            presenter: presenter
        )

        await handler.handle(action)

        XCTAssertTrue(presenter.removed.isEmpty)
        XCTAssertTrue(presenter.failures.isEmpty)
    }
}

/// Keeps the alerts that show the way `NotificationService` does: one per
/// session, all gone at a teardown, which also moves the epoch.
private final class RecordingPermissionPresenter: PermissionActionPresenting, @unchecked Sendable {
    private let lock = NSLock()
    private var epoch: UInt64 = 0
    private var shown: [String: String]
    private var _removed: [String] = []
    private var _failures: [(action: PermissionAction, body: String)] = []

    /// `shown`: the request id that each session's alert names.
    init(shown: [String: String]) {
        self.shown = shown
    }

    var removed: [String] { lock.withLock { _removed } }
    var failures: [(action: PermissionAction, body: String)] { lock.withLock { _failures } }

    func show(sessionId: String, requestId: String) {
        lock.withLock { shown[sessionId] = requestId }
    }

    /// Sign-out or an agent switch: `NotificationService.removeAll()`.
    func tearDown() {
        lock.withLock {
            epoch &+= 1
            shown = [:]
        }
    }

    func alertEpoch() -> UInt64 {
        lock.withLock { epoch }
    }

    func shownRequestId(sessionId: String) async -> String? {
        lock.withLock { shown[sessionId] }
    }

    func remove(sessionId: String) {
        lock.withLock {
            _removed.append(sessionId)
            shown[sessionId] = nil
        }
    }

    func postActionFailure(_ action: PermissionAction, body: String, epoch: UInt64) async {
        lock.withLock {
            guard self.epoch == epoch else { return }
            _failures.append((action, body))
            shown[action.sessionId] = ""
        }
    }
}
