import XCTest
@testable import Mobaide

@MainActor
final class ThemeStoreTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!
    private var preferences: PreferencesStore!

    override func setUp() {
        super.setUp()
        suiteName = "theme-store-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        preferences = PreferencesStore(defaults: defaults)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testDefaultsToSystemWhenPrefsEmpty() {
        let store = ThemeStore(preferences: preferences)

        XCTAssertEqual(store.preference, .system)
    }

    func testLoadsPersistedValue() {
        preferences.setThemePreference(.dark)

        let store = ThemeStore(preferences: preferences)

        XCTAssertEqual(store.preference, .dark)
    }

    func testSetPreferencePersistsUnderTheWebKey() {
        let store = ThemeStore(preferences: preferences)

        store.setPreference(.light)

        XCTAssertEqual(store.preference, .light)
        XCTAssertEqual(defaults.string(forKey: PreferencesStore.keyThemePreference), "light")
        XCTAssertEqual(PreferencesStore.keyThemePreference, "mobaide-theme")
    }

    func testUnknownStoredValueCoercesToSystem() {
        defaults.set("sepia", forKey: PreferencesStore.keyThemePreference)

        XCTAssertEqual(preferences.getThemePreference(), .system)
        XCTAssertEqual(ThemeStore(preferences: preferences).preference, .system)
    }
}
