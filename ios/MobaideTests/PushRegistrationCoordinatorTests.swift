import XCTest
@testable import Mobaide

@MainActor
final class PushRegistrationCoordinatorTests: XCTestCase {
    /// Records coordinator side effects; `registerResults` scripts one error
    /// (or success) per register attempt, defaulting to success.
    @MainActor
    private final class Harness {
        var registrationRequests = 0
        var registered: [String] = []
        var registerResults: [Error?] = []
        var unregisterCalls = 0
        var unregisterAvailable = true

        let coordinator: PushRegistrationCoordinator
        private let continuation: AsyncStream<AuthState>.Continuation

        /// Boxes the not-yet-constructed `self` so the coordinator's
        /// `@Sendable` closures have a Sendable capture instead of a bare
        /// mutable local var (which Swift 6 rejects as a data race).
        private final class Box: @unchecked Sendable {
            var harness: Harness?
        }

        init() {
            let (states, continuation) = AsyncStream.makeStream(of: AuthState.self)
            self.continuation = continuation
            let box = Box()
            coordinator = PushRegistrationCoordinator(
                authStates: { states },
                requestRegistration: { box.harness?.registrationRequests += 1 },
                register: { token in
                    guard let harness = box.harness else { return }
                    let result = await MainActor.run {
                        harness.registerResults.isEmpty
                            ? nil : harness.registerResults.removeFirst()
                    }
                    if let result { throw result }
                    await MainActor.run { harness.registered.append(token) }
                },
                unregisterRequest: {
                    guard box.harness?.unregisterAvailable == true else { return nil }
                    return { await MainActor.run { box.harness?.unregisterCalls += 1 } }
                }
            )
            box.harness = self
        }

        func yield(_ state: AuthState) { continuation.yield(state) }
    }

    private struct TestError: Error {}

    func testUnauthenticatedNeverRegistersNorPosts() async {
        let harness = Harness()
        harness.coordinator.start()
        harness.yield(.unauthenticated)
        harness.coordinator.tokenReceived("tok-1")
        await Task.yield()
        try? await Task.sleep(for: .milliseconds(50))

        XCTAssertEqual(harness.registrationRequests, 0)
        XCTAssertEqual(harness.registered, [])
    }

    func testRegistersOnceOnUserOnlyAndPostsWhenTokenArrives() async {
        let harness = Harness()
        harness.coordinator.start()
        harness.yield(.userOnly)
        await waitUntil { harness.registrationRequests == 1 }
        harness.coordinator.tokenReceived("tok-1")
        await waitUntil { harness.registered == ["tok-1"] }

        harness.yield(.fullyConnected)
        try? await Task.sleep(for: .milliseconds(50))
        // Same token, already posted: no duplicate POST, no re-registration.
        XCTAssertEqual(harness.registered, ["tok-1"])
        XCTAssertEqual(harness.registrationRequests, 1)
    }

    func testPostsWhenTokenArrivesBeforeAuth() async {
        let harness = Harness()
        harness.coordinator.start()
        harness.coordinator.tokenReceived("tok-early")
        harness.yield(.fullyConnected)
        await waitUntil { harness.registered == ["tok-early"] }
    }

    func testRepostsOnTokenRotation() async {
        let harness = Harness()
        harness.coordinator.start()
        harness.yield(.userOnly)
        harness.coordinator.tokenReceived("tok-1")
        await waitUntil { harness.registered == ["tok-1"] }

        harness.coordinator.tokenReceived("tok-2")
        await waitUntil { harness.registered == ["tok-1", "tok-2"] }
    }

    func testFailedPostRetriesOnNextAuthTransition() async {
        let harness = Harness()
        harness.registerResults = [TestError()]
        harness.coordinator.start()
        harness.yield(.userOnly)
        harness.coordinator.tokenReceived("tok-1")
        // First attempt fails (e.g. 404 — device row not minted yet).
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(harness.registered, [])

        harness.yield(.fullyConnected)
        await waitUntil { harness.registered == ["tok-1"] }
    }

    func testSignOutAndSignInRepostsSameToken() async {
        let harness = Harness()
        harness.coordinator.start()
        harness.yield(.userOnly)
        harness.coordinator.tokenReceived("tok-1")
        await waitUntil { harness.registered == ["tok-1"] }

        harness.yield(.unauthenticated)
        try? await Task.sleep(for: .milliseconds(20))
        harness.yield(.userOnly)
        await waitUntil { harness.registered == ["tok-1", "tok-1"] }
    }

    func testHandleSignOutFiresUnregisterAndResetsPostedState() async {
        let harness = Harness()
        harness.coordinator.start()
        harness.yield(.userOnly)
        harness.coordinator.tokenReceived("tok-1")
        await waitUntil { harness.registered == ["tok-1"] }

        harness.coordinator.handleSignOut()
        await waitUntil { harness.unregisterCalls == 1 }
    }

    func testIncompatibleServerSuspendsPosting() async {
        let harness = Harness()
        harness.coordinator.start()
        harness.yield(
            .incompatibleServer(
                AuthState.IncompatibleServer(
                    reason: "protocol",
                    minClientVersion: 7,
                    storeUrl: "https://example.com",
                    storeFallbackUrl: "https://example.com"
                )
            )
        )
        harness.coordinator.tokenReceived("tok-1")
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(harness.registered, [])
    }
}
