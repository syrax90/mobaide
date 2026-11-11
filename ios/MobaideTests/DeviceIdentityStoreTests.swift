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

    func testConcurrentFirstCallsAgreeOnOnePersistedId() async {
        let keychain = InMemoryKeychainStore(accessibility: .afterFirstUnlockThisDeviceOnly)
        let store = DeviceIdentityStore(keychain: keychain)

        let ids = await withTaskGroup(of: String.self, returning: Set<String>.self) { group in
            for _ in 0..<20 {
                group.addTask { store.getOrCreateDeviceId() }
            }
            var collected = Set<String>()
            for await id in group {
                collected.insert(id)
            }
            return collected
        }

        XCTAssertEqual(ids.count, 1, "check-then-create must be race-free")
        XCTAssertEqual(keychain.string(forKey: DeviceIdentityStore.keyDeviceUuid), ids.first)
    }
}
