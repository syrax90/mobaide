import XCTest
import Security
@testable import Mobaide

final class KeychainStoreTests: XCTestCase {
    func testAddAttributesCarryConfiguredAccessibility() {
        let standard = SecItemKeychainStore(service: "svc", accessibility: .afterFirstUnlock)
        let attributes = standard.addAttributes(forKey: "k", data: Data("v".utf8))
        XCTAssertEqual(
            attributes[kSecAttrAccessible as String] as! CFString,
            kSecAttrAccessibleAfterFirstUnlock
        )

        let deviceOnly = SecItemKeychainStore(service: "svc", accessibility: .afterFirstUnlockThisDeviceOnly)
        let deviceOnlyAttributes = deviceOnly.addAttributes(forKey: "k", data: Data("v".utf8))
        XCTAssertEqual(
            deviceOnlyAttributes[kSecAttrAccessible as String] as! CFString,
            kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        )
    }

    func testBaseQueryScopesServiceAndAccount() {
        let store = SecItemKeychainStore(service: "mobaide_auth")
        let query = store.baseQuery(forKey: "access_token")
        XCTAssertEqual(query[kSecClass as String] as! CFString, kSecClassGenericPassword)
        XCTAssertEqual(query[kSecAttrService as String] as? String, "mobaide_auth")
        XCTAssertEqual(query[kSecAttrAccount as String] as? String, "access_token")
    }

    func testInMemoryFakeSemantics() {
        let fake = InMemoryKeychainStore()
        XCTAssertNil(fake.string(forKey: "a"))

        fake.set("one", forKey: "a")
        fake.set("two", forKey: "b")
        XCTAssertEqual(fake.string(forKey: "a"), "one")

        fake.set("updated", forKey: "a")
        XCTAssertEqual(fake.string(forKey: "a"), "updated")

        fake.removeValue(forKey: "a")
        XCTAssertNil(fake.string(forKey: "a"))
        XCTAssertEqual(fake.string(forKey: "b"), "two")

        fake.removeAll()
        XCTAssertNil(fake.string(forKey: "b"))
    }
}
