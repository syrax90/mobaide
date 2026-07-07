import Foundation
import Security

/// Data-protection class for keychain items, mirroring the storage split
/// mandated by the port: agent/user credentials survive reboot-locked
/// background work (`afterFirstUnlock`), while the device identity must
/// never be backed up or transferred to another device
/// (`afterFirstUnlockThisDeviceOnly`).
enum KeychainAccessibility: Sendable {
    case afterFirstUnlock
    case afterFirstUnlockThisDeviceOnly

    var secAttrAccessible: CFString {
        switch self {
        case .afterFirstUnlock:
            return kSecAttrAccessibleAfterFirstUnlock
        case .afterFirstUnlockThisDeviceOnly:
            return kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        }
    }
}

/// Thin key-value facade over the keychain (one instance per Android
/// EncryptedSharedPreferences file). Implementations must be thread-safe.
protocol KeychainStoring: Sendable {
    var accessibility: KeychainAccessibility { get }

    func data(forKey key: String) -> Data?
    func set(_ data: Data, forKey key: String)
    func removeValue(forKey key: String)
    func removeAll()
}

extension KeychainStoring {
    func string(forKey key: String) -> String? {
        data(forKey: key).map { String(decoding: $0, as: UTF8.self) }
    }

    func set(_ string: String, forKey key: String) {
        set(Data(string.utf8), forKey: key)
    }
}
