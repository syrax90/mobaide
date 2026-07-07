import XCTest
@testable import Mobaide

final class TrustKeyPinningTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!
    private var pinning: TrustKeyPinning!

    override func setUp() {
        super.setUp()
        suiteName = "trust-pins-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        pinning = TrustKeyPinning(defaults: defaults)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testNormalization() {
        XCTAssertEqual(
            TrustKeyPinning.normalize("  HTTPS://Relay.Example.COM// "),
            "https://relay.example.com"
        )
    }

    func testFirstSeenPinsAndRecords() {
        let result = pinning.verifyOrPin(relayUrl: "https://relay.example.com", fingerprint: "fp-1")
        XCTAssertEqual(result, .pinned)
        XCTAssertEqual(pinning.getPinned(relayUrl: "https://relay.example.com"), "fp-1")
    }

    func testMatchOnSameFingerprintAcrossUrlSpellings() {
        _ = pinning.verifyOrPin(relayUrl: "https://relay.example.com", fingerprint: "fp-1")
        let result = pinning.verifyOrPin(relayUrl: "HTTPS://RELAY.EXAMPLE.COM/", fingerprint: "fp-1")
        XCTAssertEqual(result, .match, "normalized URLs share one pin")
    }

    func testMismatchFailsClosedWithoutOverwriting() {
        _ = pinning.verifyOrPin(relayUrl: "https://relay.example.com", fingerprint: "fp-1")
        let result = pinning.verifyOrPin(relayUrl: "https://relay.example.com", fingerprint: "fp-EVIL")
        XCTAssertEqual(result, .mismatch(pinned: "fp-1", scanned: "fp-EVIL"))
        XCTAssertEqual(
            pinning.getPinned(relayUrl: "https://relay.example.com"),
            "fp-1",
            "a mismatch never auto-overwrites the pin"
        )
    }

    func testSetPinnedOverwritesForExplicitRotation() {
        _ = pinning.verifyOrPin(relayUrl: "https://relay.example.com", fingerprint: "fp-1")
        pinning.setPinned(relayUrl: "https://relay.example.com", fingerprint: "fp-2")
        XCTAssertEqual(
            pinning.verifyOrPin(relayUrl: "https://relay.example.com", fingerprint: "fp-2"),
            .match
        )
    }
}
