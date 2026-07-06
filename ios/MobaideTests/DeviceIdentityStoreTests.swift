import XCTest
@testable import Mobaide

final class DeviceIdentityStoreTests: XCTestCase {
    func testGetOrCreateDeviceIdIsStable() {
        let keychain = InMemoryKeychainStore(accessibility: .afterFirstUnlockThisDeviceOnly)
        let store = DeviceIdentityStore(keychain: keychain)

        let first = store.getOrCreateDeviceId()
        let second = store.getOrCreateDeviceId()

        XCTAssertFalse(first.isEmpty)
        XCTAssertEqual(first, second)
        XCTAssertNotNil(UUID(uuidString: first))
    }

    func testWiredAccessibilityIsThisDeviceOnly() {
        let keychain = InMemoryKeychainStore(accessibility: .afterFirstUnlockThisDeviceOnly)
        let store = DeviceIdentityStore(keychain: keychain)
        XCTAssertEqual(store.keychain.accessibility, .afterFirstUnlockThisDeviceOnly)
    }

    func testDeviceNameIsNonEmptyAppleIdentifier() {
        let store = DeviceIdentityStore(keychain: InMemoryKeychainStore())
        XCTAssertTrue(store.getDeviceName().hasPrefix("Apple"))
    }
}
