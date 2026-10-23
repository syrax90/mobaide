import XCTest
@testable import Mobaide

@MainActor
final class RouterTests: XCTestCase {
    func testNavigatePopAndPopToRoot() {
        let router = Router()
        router.navigate(to: .sessionList)
        router.navigate(to: .sessionDetail(sessionId: "s1"))
        XCTAssertEqual(router.path, [.sessionList, .sessionDetail(sessionId: "s1")])

        router.pop()
        XCTAssertEqual(router.path, [.sessionList])

        router.navigate(to: .settings)
        router.popToRoot()
        XCTAssertEqual(router.path, [])
    }

    func testPopToTruncatesEverythingAboveTheRoute() {
        let router = Router()
        router.navigate(to: .sessionList)
        router.navigate(to: .sessionDetail(sessionId: "s1"))
        router.navigate(to: .settings)

        router.popTo(.sessionList)

        XCTAssertEqual(router.path, [.sessionList])
    }

    func testPopToPushesTheRouteWhenItIsNotOnTheStack() {
        let router = Router()
        // A notification deep link lands straight on the terminal with nothing
        // under it; leaving a dead session must still reach the list.
        router.navigate(to: .sessionDetail(sessionId: "s1"))

        router.popTo(.sessionList)

        XCTAssertEqual(router.path, [.sessionList])
    }

    func testPopToUnwindsToTheNearestOccurrence() {
        let router = Router()
        router.navigate(to: .sessionList)
        router.navigate(to: .sessionDetail(sessionId: "s1"))
        router.navigate(to: .sessionList)
        router.navigate(to: .sessionDetail(sessionId: "s2"))

        router.popTo(.sessionList)

        XCTAssertEqual(
            router.path, [.sessionList, .sessionDetail(sessionId: "s1"), .sessionList],
            "unwinding to the first occurrence would drop screens the user walked through"
        )
    }

    func testTransitionToUnauthenticatedClearsPath() {
        let router = Router()
        router.navigate(to: .settings)
        router.handleTransition(from: .userOnly, to: .unauthenticated)
        XCTAssertEqual(router.path, [])
    }

    func testAgentDisconnectClearsPath() {
        let router = Router()
        router.navigate(to: .sessionList)
        router.handleTransition(from: .fullyConnected, to: .userOnly)
        XCTAssertEqual(router.path, [])
    }

    func testPromotionPreservesPath() {
        let router = Router()
        router.navigate(to: .connections)
        router.handleTransition(from: .userOnly, to: .fullyConnected)
        XCTAssertEqual(router.path, [.connections])
    }

    func testIncompatibleServerTransitionPreservesPath() {
        let router = Router()
        router.navigate(to: .settings)
        let info = AuthState.IncompatibleServer(
            reason: "r", minClientVersion: 1, storeUrl: "", storeFallbackUrl: ""
        )
        router.handleTransition(from: .fullyConnected, to: .incompatibleServer(info))
        XCTAssertEqual(router.path, [.settings], "the takeover renders above the stack; no reset needed")
    }

    func testReplaceLastSwapsTheTopScreenWithoutGrowingTheStack() {
        let router = Router()
        router.navigate(to: .sessionList)
        router.navigate(to: .sessionDetail(sessionId: "s1"))

        // The sessions drawer's switch: Back must still return to the list,
        // not to the terminal the user just left.
        router.replaceLast(with: .sessionDetail(sessionId: "s2"))

        XCTAssertEqual(router.path, [.sessionList, .sessionDetail(sessionId: "s2")])
    }

    func testReplaceLastOnAnEmptyPathPushes() {
        let router = Router()

        router.replaceLast(with: .sessionDetail(sessionId: "s1"))

        XCTAssertEqual(router.path, [.sessionDetail(sessionId: "s1")], "nothing to replace still has to land somewhere")
    }
}
