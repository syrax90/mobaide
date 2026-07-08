import Foundation
import Observation

/// Device list + revoke (Android `DevicesViewModel`). Revoke is optimistic on
/// iOS (the issue's acceptance criterion): the row is removed immediately and
/// re-inserted if the DELETE fails. The current device is matched against the
/// local `DeviceIdentityStore` id (the payload carries no `is_current`).
@MainActor
@Observable
final class DevicesModel {
    private(set) var devices: [DeviceResponse] = []
    private(set) var isLoading = false
    var revokeTarget: DeviceResponse?
    var message: String?

    let currentDeviceId: String

    private let deviceRepository: DeviceRepository

    init(deviceRepository: DeviceRepository, deviceIdentityStore: DeviceIdentityStore) {
        self.deviceRepository = deviceRepository
        self.currentDeviceId = deviceIdentityStore.getOrCreateDeviceId()
    }

    var isEmpty: Bool { devices.isEmpty && !isLoading }

    func isCurrent(_ device: DeviceResponse) -> Bool {
        device.device_id == currentDeviceId
    }

    func refresh() async {
        isLoading = true
        defer { isLoading = false }
        do {
            devices = try await deviceRepository.listDevices()
        } catch {
            message = "Failed to load devices: \(ErrorText.message(for: error))"
        }
    }

    func confirmRevoke(_ device: DeviceResponse) {
        revokeTarget = nil
        let originalIndex = devices.firstIndex { $0.device_id == device.device_id }
        devices.removeAll { $0.device_id == device.device_id }
        Task { [weak self] in
            guard let self else { return }
            do {
                try await self.deviceRepository.revokeDevice(deviceId: device.device_id)
                // A refresh in flight during the DELETE may have re-added the
                // row from a pre-revoke server list; keep the revoked row gone.
                self.devices.removeAll { $0.device_id == device.device_id }
            } catch {
                // Reinsert only the failed row — restoring a whole pre-revoke
                // snapshot could resurrect a device a concurrent revoke removed.
                if !self.devices.contains(where: { $0.device_id == device.device_id }) {
                    let target = min(originalIndex ?? self.devices.count, self.devices.count)
                    self.devices.insert(device, at: target)
                }
                self.message = "Failed to revoke device: \(ErrorText.message(for: error))"
            }
        }
    }

    func clearMessage() { message = nil }
}
