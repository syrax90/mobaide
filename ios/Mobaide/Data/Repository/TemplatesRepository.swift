import Foundation

/// App-scoped live template list (Android `TemplatesRepository`). The
/// observable cache is mutated by REST responses and by `template.*` pushes
/// that `UserEventBus` routes here through `TemplateSyncApplying`. Upserts key
/// on `template_id` only (idempotent; the server is the single writer, so
/// `version` is never compared).
@MainActor
final class TemplatesRepository: TemplateSyncApplying {
    private let centralAPI: CentralAPI
    private let userIdentityStore: UserIdentityStore
    private let broadcaster = Broadcaster<[TemplateDto]>.stateFlow()

    private(set) var currentTemplates: [TemplateDto] = []

    init(centralAPI: CentralAPI, userIdentityStore: UserIdentityStore) {
        self.centralAPI = centralAPI
        self.userIdentityStore = userIdentityStore
        broadcaster.emit([])
    }

    func templates() -> AsyncStream<[TemplateDto]> {
        broadcaster.subscribe()
    }

    private func setTemplates(_ list: [TemplateDto]) {
        currentTemplates = list
        broadcaster.emit(list)
    }

    // MARK: - TemplateSyncApplying

    func refreshTemplates() async throws {
        // Account-generation guard (`ShortcutsRepository.refreshShortcuts`):
        // a response for the previous account must not repopulate the cache
        // after sign-out or an account switch cleared it.
        let requestUserId = userIdentityStore.getUserId()
        let list = try await centralAPI.listTemplates(userToken: try userToken())
        guard userIdentityStore.getUserId() == requestUserId else { return }
        setTemplates(list)
    }

    func applyUpserted(_ template: TemplateDto) async {
        var list = currentTemplates
        if let index = list.firstIndex(where: { $0.template_id == template.template_id }) {
            list[index] = template
        } else {
            list.append(template)
        }
        setTemplates(list)
    }

    func applyDeleted(templateId: String) async {
        setTemplates(currentTemplates.filter { $0.template_id != templateId })
    }

    func clear() async {
        setTemplates([])
    }

    // MARK: - Mutations

    // Same account-generation guard as `refreshTemplates`: a response landing
    // after an account switch still returns the server row, but skips the
    // cache write the new account observes.

    @discardableResult
    func createTemplate(name: String, body: String) async throws -> TemplateDto {
        let requestUserId = userIdentityStore.getUserId()
        let created = try await centralAPI.createTemplate(userToken: try userToken(), name: name, body: body)
        if userIdentityStore.getUserId() == requestUserId {
            await applyUpserted(created)
        }
        return created
    }

    @discardableResult
    func updateTemplate(templateId: String, update: UpdateTemplateRequest) async throws -> TemplateDto {
        let requestUserId = userIdentityStore.getUserId()
        let updated = try await centralAPI.updateTemplate(
            userToken: try userToken(),
            templateId: templateId,
            update: update
        )
        if userIdentityStore.getUserId() == requestUserId {
            await applyUpserted(updated)
        }
        return updated
    }

    func deleteTemplate(templateId: String) async throws {
        let requestUserId = userIdentityStore.getUserId()
        try await centralAPI.deleteTemplate(userToken: try userToken(), templateId: templateId)
        guard userIdentityStore.getUserId() == requestUserId else { return }
        await applyDeleted(templateId: templateId)
    }

    private func userToken() throws -> String {
        guard let token = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: -1, errorBody: "Not signed in")
        }
        return token
    }
}
