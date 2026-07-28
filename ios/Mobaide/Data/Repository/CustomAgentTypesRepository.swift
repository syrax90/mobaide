import Foundation

/// App-scoped live custom-agent-type list (Android `CustomAgentTypesRepository`).
/// The observable cache holds the account's types in server order and is
/// mutated by REST responses and by `custom_type.*` pushes that `UserEventBus`
/// routes here through `CustomTypeSyncApplying`. Upserts key on `id` only
/// (idempotent; the server is the single writer).
@MainActor
final class CustomAgentTypesRepository: CustomTypeSyncApplying {
    private let centralAPI: CentralAPI
    private let userIdentityStore: UserIdentityStore
    private let broadcaster = Broadcaster<[CustomAgentTypeDto]>.stateFlow()

    private(set) var currentTypes: [CustomAgentTypeDto] = []

    init(centralAPI: CentralAPI, userIdentityStore: UserIdentityStore) {
        self.centralAPI = centralAPI
        self.userIdentityStore = userIdentityStore
        broadcaster.emit([])
    }

    func types() -> AsyncStream<[CustomAgentTypeDto]> {
        broadcaster.subscribe()
    }

    private func setTypes(_ list: [CustomAgentTypeDto]) {
        currentTypes = list
        broadcaster.emit(list)
    }

    // MARK: - CustomTypeSyncApplying

    func refreshCustomTypes() async throws {
        // Account-generation guard: a refresh started for one account must not
        // apply after logout cleared the cache (or after another account
        // signed in) — the stale response would silently repopulate it. The
        // user id is the generation marker (unlike the token, it survives
        // token refresh mid-flight).
        let requestUserId = userIdentityStore.getUserId()
        let list = try await centralAPI.listCustomAgentTypes(userToken: try userToken())
        guard userIdentityStore.getUserId() == requestUserId else { return }
        setTypes(list)
    }

    func applyUpserted(_ type: CustomAgentTypeDto) async {
        var list = currentTypes
        if let index = list.firstIndex(where: { $0.id == type.id }) {
            list[index] = type
        } else {
            list.append(type)
        }
        setTypes(list)
    }

    func applyDeleted(typeId: String) async {
        setTypes(currentTypes.filter { $0.id != typeId })
    }

    func clear() async {
        setTypes([])
    }

    // MARK: - Mutations

    // Mutations carry the same account-generation guard as
    // `refreshCustomTypes`: their Tasks outlive the screen, so a response
    // landing after an account switch must not write the previous account's
    // row into the cache the new account observes. The server row is still
    // returned on mismatch — only the cache write is skipped.

    @discardableResult
    func createType(name: String, command: String) async throws -> CustomAgentTypeDto {
        let requestUserId = userIdentityStore.getUserId()
        let created = try await centralAPI.createCustomAgentType(
            userToken: try userToken(),
            name: name,
            command: command
        )
        if userIdentityStore.getUserId() == requestUserId {
            await applyUpserted(created)
        }
        return created
    }

    @discardableResult
    func updateType(typeId: String, name: String, command: String) async throws -> CustomAgentTypeDto {
        let requestUserId = userIdentityStore.getUserId()
        let updated = try await centralAPI.updateCustomAgentType(
            userToken: try userToken(),
            typeId: typeId,
            update: UpdateCustomAgentTypeRequest(name: name, command: command)
        )
        if userIdentityStore.getUserId() == requestUserId {
            await applyUpserted(updated)
        }
        return updated
    }

    func deleteType(typeId: String) async throws {
        let requestUserId = userIdentityStore.getUserId()
        try await centralAPI.deleteCustomAgentType(userToken: try userToken(), typeId: typeId)
        guard userIdentityStore.getUserId() == requestUserId else { return }
        await applyDeleted(typeId: typeId)
    }

    private func userToken() throws -> String {
        guard let token = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: -1, errorBody: "Not signed in")
        }
        return token
    }
}
