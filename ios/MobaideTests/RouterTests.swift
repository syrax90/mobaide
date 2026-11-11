import XCTest
@testable import Mobaide

@MainActor
final class RouterTests: XCTestCase {
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

    func testReplaceLastSwapsTheTopScreenWithoutGrowingTheStack() {
        let router = Router()
        router.navigate(to: .sessionList)
        router.navigate(to: .sessionDetail(sessionId: "s1"))

        // The sessions drawer's switch: Back must still return to the list,
        // not to the terminal the user just left.
        router.replaceLast(with: .sessionDetail(sessionId: "s2"))

        XCTAssertEqual(router.path, [.sessionList, .sessionDetail(sessionId: "s2")])
    }
}
