import Foundation

/// App-scoped live custom-agent-type list (Android `CustomAgentTypesRepository`).
/// The observable cache holds the account's types in server order and is
/// mutated by REST responses and by `custom_type.*` pushes that `UserEventBus`
/// routes here through `CustomTypeSyncApplying`. Upserts key on `id` only
/// (idempotent; the server is the single writer).
///
/// It also holds the account's order of the mode picker's cards (built-in
/// modes and custom types, by mode id), from `GET /api/session-mode-order` and
/// the `session_mode_order.updated` push. `modeOrder()` streams the order to
/// show: a save in flight shows its own order until the server confirms it.
@MainActor
final class CustomAgentTypesRepository: CustomTypeSyncApplying {
    private let centralAPI: CentralAPI
    private let userIdentityStore: UserIdentityStore
    private let broadcaster = Broadcaster<[CustomAgentTypeDto]>.stateFlow()

    private(set) var currentTypes: [CustomAgentTypeDto] = []

    private let modeOrderBroadcaster = Broadcaster<[String]>.stateFlow()
    private var serverModeOrder = SessionModeOrderDto.empty
    private var pendingModeOrder: [String]?
    /// The last save in the chain; each new save waits for it, so saves reach
    /// the server in the order the user made them.
    private var modeOrderSaveTail: Task<Void, Never>?
    private(set) var currentModeOrder: [String] = []

    init(centralAPI: CentralAPI, userIdentityStore: UserIdentityStore) {
        self.centralAPI = centralAPI
        self.userIdentityStore = userIdentityStore
        broadcaster.emit([])
        modeOrderBroadcaster.emit([])
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
        serverModeOrder = .empty
        pendingModeOrder = nil
        publishModeOrder()
    }

    // MARK: - Mode-picker order

    func modeOrder() -> AsyncStream<[String]> {
        modeOrderBroadcaster.subscribe()
    }

    func refreshModeOrder() async throws {
        let requestUserId = userIdentityStore.getUserId()
        let order = try await centralAPI.getSessionModeOrder(userToken: try userToken())
        guard userIdentityStore.getUserId() == requestUserId else { return }
        await applyModeOrder(order)
    }

    /// Apply a server snapshot; one older than the cached version is dropped.
    func applyModeOrder(_ order: SessionModeOrderDto) async {
        guard order.version >= serverModeOrder.version else { return }
        serverModeOrder = order
        if pendingModeOrder == order.order { pendingModeOrder = nil }
        publishModeOrder()
    }

    /// Save `order` and show it at once. The save runs in its own task, so it
    /// finishes even if the wizard closes first. On failure the order falls
    /// back to the last one the server confirmed, and the error is rethrown.
    func saveModeOrder(_ order: [String]) async throws {
        let requestUserId = userIdentityStore.getUserId()
        pendingModeOrder = order
        publishModeOrder()
        let previous = modeOrderSaveTail
        let save = Task { @MainActor () -> Error? in
            await previous?.value
            defer { self.releasePendingModeOrder(order) }
            // A save queued before a sign-out must not run with the next
            // account's token and write this account's order into it.
            guard self.userIdentityStore.getUserId() == requestUserId else { return nil }
            do {
                let saved = try await self.centralAPI.putSessionModeOrder(
                    userToken: try self.userToken(),
                    order: order
                )
                if self.userIdentityStore.getUserId() == requestUserId {
                    await self.applyModeOrder(saved)
                }
                return nil
            } catch {
                return error
            }
        }
        modeOrderSaveTail = Task { _ = await save.value }
        if let error = await save.value { throw error }
    }

    private func releasePendingModeOrder(_ order: [String]) {
        guard pendingModeOrder == order else { return }
        pendingModeOrder = nil
        publishModeOrder()
    }

    private func publishModeOrder() {
        let order = pendingModeOrder ?? serverModeOrder.order
        guard order != currentModeOrder else { return }
        currentModeOrder = order
        modeOrderBroadcaster.emit(order)
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
