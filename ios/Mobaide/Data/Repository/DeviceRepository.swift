import Foundation

/// Stateless device-management calls (Android `DeviceRepository`). Devices
/// have no live event channel, so this holds no cache — the screen loads on
/// appearance and revokes on demand.
struct DeviceRepository: Sendable {
    let centralAPI: CentralAPI
    let userIdentityStore: UserIdentityStore

    func listDevices() async throws -> [DeviceResponse] {
        try await centralAPI.listDevices(userToken: try userToken())
    }

    func revokeDevice(deviceId: String) async throws {
        try await centralAPI.revokeDevice(userToken: try userToken(), deviceId: deviceId)
    }

    func registerPushToken(deviceId: String, token: String, environment: String) async throws {
        try await centralAPI.registerPushToken(
            userToken: try userToken(),
            deviceId: deviceId,
            request: RegisterPushTokenRequest(platform: "ios", token: token, environment: environment)
        )
    }

    private func userToken() throws -> String {
        guard let token = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: -1, errorBody: "Not signed in")
        }
        return token
    }
}
