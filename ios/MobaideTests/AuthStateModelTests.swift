import XCTest
@testable import Mobaide

final class AuthStateModelTests: XCTestCase {
    private struct Timeout: Error {}

    /// Deadline-poll on the main actor until `condition` holds (mirrors the
    /// StreamCollector drain cadence).
    @MainActor
    private func waitForState(
        timeout: Duration = .seconds(5),
        _ condition: () -> Bool
    ) async throws {
        let deadline = ContinuousClock.now.advanced(by: timeout)
        while ContinuousClock.now < deadline {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(1))
        }
        throw Timeout()
    }

    @MainActor
    func testBindReplaysLatestStateWhenTransitionPrecedesBind() async throws {
        let harness = SessionHarness()
        // Cold-start race: the transition lands before the UI binds.
        await harness.sessionManager.markUserLoggedIn()

        let model = AuthStateModel(initial: .unauthenticated)
        model.bind(to: harness.sessionManager)

        try await waitForState { model.state == .userOnly }
    }

    @MainActor
    func testBindFollowsTransitionsAfterBind() async throws {
        let harness = SessionHarness()
        let model = AuthStateModel(initial: .unauthenticated)
        model.bind(to: harness.sessionManager)
        XCTAssertEqual(model.state, .unauthenticated)

        await harness.sessionManager.markUserLoggedIn()
        try await waitForState { model.state == .userOnly }

        await harness.sessionManager.markAgentConnected()
        try await waitForState { model.state == .fullyConnected }
    }
}
