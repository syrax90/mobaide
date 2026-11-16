import XCTest
@testable import Mobaide

/// Which finished turns the user has seen, per device. A restart must not turn
/// every finished agent back into "Done".
@MainActor
final class HerdrSeenStoreTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "herdr-seen-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testTheNewestTurnOfAPaneSurvivesARestart() {
        let store = HerdrSeenStore(defaults: defaults)
        store.markSeen(key: "pane-a", turn: 3)
        store.markSeen(key: "pane-a", turn: 4)
        store.markSeen(key: "pane-b", turn: 1)

        let restarted = HerdrSeenStore(defaults: defaults)

        XCTAssertEqual(restarted.seen, ["pane-a": 4, "pane-b": 1])
    }

    func testThePaneJustSeenSurvivesTheCap() {
        let store = HerdrSeenStore(defaults: defaults)
        for index in 0..<500 {
            store.markSeen(key: "pane-\(index)", turn: Int64(index))
        }

        store.markSeen(key: "pane-new", turn: 7)

        XCTAssertEqual(store.seen.count, 500)
        XCTAssertEqual(store.seen["pane-new"], 7)
        XCTAssertEqual(HerdrSeenStore(defaults: defaults).seen["pane-new"], 7)
    }
}
