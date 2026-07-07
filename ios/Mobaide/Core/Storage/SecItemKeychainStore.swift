import Foundation
import Security

/// SecItem-backed `KeychainStoring`. Each instance owns one
/// `kSecAttrService` namespace — the iOS analogue of one Android
/// EncryptedSharedPreferences file — so `removeAll()` wipes exactly that
/// store and nothing else.
struct SecItemKeychainStore: KeychainStoring {
    let service: String
    let accessibility: KeychainAccessibility

    init(service: String, accessibility: KeychainAccessibility = .afterFirstUnlock) {
        self.service = service
        self.accessibility = accessibility
    }

    func data(forKey key: String) -> Data? {
        var query = baseQuery(forKey: key)
        query[kSecReturnData as String] = kCFBooleanTrue
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess else { return nil }
        return result as? Data
    }

    func set(_ data: Data, forKey key: String) {
        SecItemDelete(baseQuery(forKey: key) as CFDictionary)
        SecItemAdd(addAttributes(forKey: key, data: data) as CFDictionary, nil)
    }

    func removeValue(forKey key: String) {
        SecItemDelete(baseQuery(forKey: key) as CFDictionary)
    }

    func removeAll() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
        ]
        SecItemDelete(query as CFDictionary)
    }

    func baseQuery(forKey key: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
    }

    func addAttributes(forKey key: String, data: Data) -> [String: Any] {
        var attributes = baseQuery(forKey: key)
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = accessibility.secAttrAccessible
        return attributes
    }
}
