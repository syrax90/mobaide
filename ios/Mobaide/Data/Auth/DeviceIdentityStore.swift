import Foundation
import os

/// Mirror of Android `DeviceIdentityStore`: a stable per-install device
/// UUID used for device management and pairing. Stored in the keychain
/// with **ThisDeviceOnly** accessibility so it is never backed up or
/// migrated to another device — a restore must not clone the device id
/// (Android achieves the same via backup-exclusion rules).
struct DeviceIdentityStore: Sendable {
    static let service = "device_identity"
    static let keyDeviceUuid = "device_uuid"

    let keychain: any KeychainStoring

    // Serializes the check-then-create sequence so concurrent first-run
    // callers all get the one persisted UUID (Android parity: DataStore
    // `edit` transaction).
    private let creationLock = OSAllocatedUnfairLock()

    init(keychain: any KeychainStoring) {
        self.keychain = keychain
    }

    func getOrCreateDeviceId() -> String {
        creationLock.withLock {
            if let existing = keychain.string(forKey: Self.keyDeviceUuid) {
                return existing
            }
            let fresh = UUID().uuidString
            keychain.set(fresh, forKey: Self.keyDeviceUuid)
            return fresh
        }
    }

    /// Human-readable device name shown in device management, e.g.
    /// "Apple iPhone17,2" (Android parity: "$manufacturer $model").
    func getDeviceName() -> String {
        let model = Self.hardwareModelIdentifier()
        return model.isEmpty ? "Apple device" : "Apple \(model)"
    }

    static func hardwareModelIdentifier() -> String {
        var systemInfo = utsname()
        uname(&systemInfo)
        return withUnsafeBytes(of: &systemInfo.machine) { rawBuffer in
            guard let base = rawBuffer.baseAddress else { return "" }
            return String(cString: base.assumingMemoryBound(to: CChar.self))
        }
    }
}
