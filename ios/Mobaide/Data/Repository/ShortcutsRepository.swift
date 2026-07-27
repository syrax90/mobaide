import Foundation

/// App-scoped live saved-shortcut list (Android `ShortcutsRepository`). The
/// observable cache holds every kind's shortcuts in server order and is
/// mutated by REST responses and by `shortcut.*` pushes that `UserEventBus`
/// routes here through `ShortcutSyncApplying`; per-kind filtering and sorting
/// is the consumer's job. Upserts key on `shortcut_id` only (idempotent; the
/// server is the single writer, so `version` is never compared).
@MainActor
final class ShortcutsRepository: ShortcutSyncApplying {
    private let centralAPI: CentralAPI
    private let userIdentityStore: UserIdentityStore
    private let broadcaster = Broadcaster<[ShortcutDto]>.stateFlow()

    private(set) var currentShortcuts: [ShortcutDto] = []

    init(centralAPI: CentralAPI, userIdentityStore: UserIdentityStore) {
        self.centralAPI = centralAPI
        self.userIdentityStore = userIdentityStore
        broadcaster.emit([])
    }

    func shortcuts() -> AsyncStream<[ShortcutDto]> {
        broadcaster.subscribe()
    }

    private func setShortcuts(_ list: [ShortcutDto]) {
        currentShortcuts = list
        broadcaster.emit(list)
    }

    // MARK: - ShortcutSyncApplying

    func refreshShortcuts() async throws {
        // Account-generation guard: a refresh started for one account must not
        // apply after logout cleared the cache (or after another account
        // signed in) — the stale response would silently repopulate it. The
        // user id is the generation marker (unlike the token, it survives
        // token refresh mid-flight).
        let requestUserId = userIdentityStore.getUserId()
        let list = try await centralAPI.listShortcuts(userToken: try userToken())
        guard userIdentityStore.getUserId() == requestUserId else { return }
        setShortcuts(list)
    }

    func applyUpserted(_ shortcut: ShortcutDto) async {
        var list = currentShortcuts
        if let index = list.firstIndex(where: { $0.shortcut_id == shortcut.shortcut_id }) {
            list[index] = shortcut
        } else {
            list.append(shortcut)
        }
        setShortcuts(list)
    }

    func applyDeleted(shortcutId: String) async {
        setShortcuts(currentShortcuts.filter { $0.shortcut_id != shortcutId })
    }

    func clear() async {
        setShortcuts([])
    }

    // MARK: - Mutations

    // Mutations carry the same account-generation guard as `refreshShortcuts`:
    // their Tasks outlive the screen, so a response landing after an account
    // switch must not write the previous account's row into the cache the new
    // account observes. The server row is still returned on mismatch — only
    // the cache write is skipped.

    @discardableResult
    func createShortcut(kind: String, chordToken: String, name: String? = nil) async throws -> ShortcutDto {
        let requestUserId = userIdentityStore.getUserId()
        let created = try await centralAPI.createShortcut(
            userToken: try userToken(),
            kind: kind,
            chordToken: chordToken,
            name: name
        )
        if userIdentityStore.getUserId() == requestUserId {
            await applyUpserted(created)
        }
        return created
    }

    @discardableResult
    func renameShortcut(shortcutId: String, name: String) async throws -> ShortcutDto {
        let requestUserId = userIdentityStore.getUserId()
        let updated = try await centralAPI.updateShortcut(
            userToken: try userToken(),
            shortcutId: shortcutId,
            update: UpdateShortcutRequest(name: name)
        )
        if userIdentityStore.getUserId() == requestUserId {
            await applyUpserted(updated)
        }
        return updated
    }

    func deleteShortcut(shortcutId: String) async throws {
        let requestUserId = userIdentityStore.getUserId()
        try await centralAPI.deleteShortcut(userToken: try userToken(), shortcutId: shortcutId)
        guard userIdentityStore.getUserId() == requestUserId else { return }
        await applyDeleted(shortcutId: shortcutId)
    }

    private func userToken() throws -> String {
        guard let token = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: -1, errorBody: "Not signed in")
        }
        return token
    }
}
