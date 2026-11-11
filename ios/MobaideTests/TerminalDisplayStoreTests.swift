import XCTest
@testable import Mobaide

@MainActor
final class TerminalDisplayStoreTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!
    private var preferences: PreferencesStore!

    override func setUp() {
        super.setUp()
        suiteName = "terminal-display-store-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        preferences = PreferencesStore(defaults: defaults)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testInitLoadsPersistedValues() {
        preferences.setTerminalDisplayMode(.full)
        preferences.setTerminalStaticColumns(200)

        let store = TerminalDisplayStore(preferences: preferences)

        XCTAssertEqual(store.mode, .full)
        XCTAssertEqual(store.staticColumns, 200)
        XCTAssertEqual(store.config, TerminalDisplayConfig(mode: .full, staticColumns: 200))
    }

    func testSetModeWritesThroughToPreferences() {
        let store = TerminalDisplayStore(preferences: preferences)

        store.setMode(.full)

        XCTAssertEqual(store.mode, .full)
        XCTAssertEqual(store.config.mode, .full)
        XCTAssertEqual(
            PreferencesStore(defaults: defaults).getTerminalDisplayMode(), .full,
            "a freshly constructed store reads the persisted mode"
        )
    }

    func testSetModeNoOpWhenUnchangedSkipsPersistence() {
        let store = TerminalDisplayStore(preferences: preferences)

        store.setMode(.static)

        XCTAssertEqual(store.mode, .static)
        XCTAssertNil(
            defaults.string(forKey: PreferencesStore.keyTerminalDisplayMode),
            "setting the current mode must not write to the backing store"
        )
    }

    func testSetStaticColumnsClampsAndWritesThrough() {
        let store = TerminalDisplayStore(preferences: preferences)

        store.setStaticColumns(5000)
        XCTAssertEqual(store.staticColumns, 240, "above-max clamps down")
        XCTAssertEqual(store.config.staticColumns, 240)

        store.setStaticColumns(10)
        XCTAssertEqual(store.staticColumns, 60, "below-min clamps up")
        XCTAssertEqual(
            PreferencesStore(defaults: defaults).getTerminalStaticColumns(), 60,
            "the clamped value is persisted"
        )
    }
}
