import XCTest
@testable import Mobaide

final class PreferencesStoreConversionTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!
    private var preferences: PreferencesStore!

    override func setUp() {
        super.setUp()
        suiteName = "prefs-conversion-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        preferences = PreferencesStore(defaults: defaults)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testOutcomeRoundTripsPerUser() {
        XCTAssertNil(preferences.getAgentPairedOutcome(userId: "u1"))

        preferences.setAgentPairedOutcome(userId: "u1", outcome: "logged")

        XCTAssertEqual(preferences.getAgentPairedOutcome(userId: "u1"), "logged")
        XCTAssertNil(preferences.getAgentPairedOutcome(userId: "u2"))
    }

    func testArmingKeepsTheFirstValue() {
        let first = Date(timeIntervalSince1970: 1_700_000_000)
        let later = first.addingTimeInterval(3600)

        XCTAssertNil(preferences.getConversionArmedAt())
        XCTAssertEqual(preferences.armConversionIfUnset(now: first), first)
        XCTAssertEqual(preferences.armConversionIfUnset(now: later), first)
        XCTAssertEqual(preferences.getConversionArmedAt(), first)
    }

    func testSignOutSweepsLeaveTheConversionKeys() {
        let armed = Date(timeIntervalSince1970: 1_700_000_000)
        preferences.setAgentPairedOutcome(userId: "u1", outcome: "logged")
        _ = preferences.armConversionIfUnset(now: armed)
        preferences.addRecentCustomPath(agentKey: "agent-1", path: "/tmp/x")
        preferences.setAttentionSeen(agentKey: "agent-1", record: AttentionSeenRecord(baselined: true))

        preferences.removeAllRecentCustomPaths()
        preferences.removeAllAttentionSeen()

        XCTAssertEqual(preferences.getRecentCustomPaths(agentKey: "agent-1"), [])
        XCTAssertEqual(preferences.getAttentionSeen(agentKey: "agent-1"), AttentionSeenRecord())
        XCTAssertEqual(preferences.getAgentPairedOutcome(userId: "u1"), "logged")
        XCTAssertEqual(preferences.getConversionArmedAt(), armed)
    }
}
