import XCTest
@testable import Mobaide

final class ConversionStateStoreTests: XCTestCase {
    private var directory: URL!
    private var store: ConversionStateStore!

    override func setUp() {
        super.setUp()
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("conversion-state-\(UUID().uuidString)", isDirectory: true)
        store = ConversionStateStore(directory: directory)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: directory)
        super.tearDown()
    }

    func testOutcomeRoundTripsPerUser() {
        XCTAssertNil(store.getOutcome(userId: "u1"))

        store.setOutcome(userId: "u1", outcome: "logged")

        XCTAssertEqual(store.getOutcome(userId: "u1"), "logged")
        XCTAssertNil(store.getOutcome(userId: "u2"))
    }

    func testArmingKeepsTheFirstValue() {
        let first = Date(timeIntervalSince1970: 1_700_000_000)
        let later = first.addingTimeInterval(3600)

        XCTAssertNil(store.getArmedAt())
        XCTAssertEqual(store.armIfUnset(now: first), first)
        XCTAssertEqual(store.armIfUnset(now: later), first)
        XCTAssertEqual(store.getArmedAt(), first)
    }

    func testStateSurvivesANewInstanceOnTheSameDirectory() {
        _ = store.armIfUnset(now: Date(timeIntervalSince1970: 1_700_000_000))
        store.setOutcome(userId: "u1", outcome: "skipped_predates_install")

        let reopened = ConversionStateStore(directory: directory)

        XCTAssertEqual(reopened.getArmedAt(), Date(timeIntervalSince1970: 1_700_000_000))
        XCTAssertEqual(reopened.getOutcome(userId: "u1"), "skipped_predates_install")
    }

    func testFileIsExcludedFromBackup() throws {
        store.setOutcome(userId: "u1", outcome: "logged")

        let file = directory.appendingPathComponent(ConversionStateStore.fileName)
        let values = try file.resourceValues(forKeys: [.isExcludedFromBackupKey])
        XCTAssertEqual(values.isExcludedFromBackup, true)
    }

    func testAFreshDirectoryStartsEmpty() {
        let other = ConversionStateStore(
            directory: FileManager.default.temporaryDirectory
                .appendingPathComponent("conversion-state-\(UUID().uuidString)", isDirectory: true)
        )

        XCTAssertNil(other.getArmedAt())
        XCTAssertNil(other.getOutcome(userId: "u1"))
    }
}
