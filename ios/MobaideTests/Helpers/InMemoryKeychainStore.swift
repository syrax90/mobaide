import Foundation
@testable import Mobaide

/// Deterministic `KeychainStoring` fake — behavior tests never touch the
/// real (persistent, cross-run) keychain. Records the accessibility it was
/// configured with so wiring tests can assert the data-protection class.
final class InMemoryKeychainStore: KeychainStoring, @unchecked Sendable {
    let accessibility: KeychainAccessibility

    private let lock = NSLock()
    private var storage: [String: Data] = [:]
    private var writeOrderStorage: [String] = []

    init(accessibility: KeychainAccessibility = .afterFirstUnlock) {
        self.accessibility = accessibility
    }

    /// Keys in the order they were written (persist-before-flip assertions).
    var writeOrder: [String] {
        lock.withLock { writeOrderStorage }
    }

    var allKeys: [String] {
        lock.withLock { Array(storage.keys) }
    }

    func data(forKey key: String) -> Data? {
        lock.withLock { storage[key] }
    }

    func set(_ data: Data, forKey key: String) {
        lock.withLock {
            storage[key] = data
            writeOrderStorage.append(key)
        }
    }

    func removeValue(forKey key: String) {
        lock.withLock { _ = storage.removeValue(forKey: key) }
    }

    func removeAll() {
        lock.withLock { storage.removeAll() }
    }
}
