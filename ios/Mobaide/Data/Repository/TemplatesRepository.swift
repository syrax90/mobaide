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
        setTemplates(try await centralAPI.listTemplates(userToken: try userToken()))
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

    @discardableResult
    func createTemplate(name: String, body: String) async throws -> TemplateDto {
        let created = try await centralAPI.createTemplate(userToken: try userToken(), name: name, body: body)
        await applyUpserted(created)
        return created
    }

    @discardableResult
    func updateTemplate(templateId: String, update: UpdateTemplateRequest) async throws -> TemplateDto {
        let updated = try await centralAPI.updateTemplate(
            userToken: try userToken(),
            templateId: templateId,
            update: update
        )
        await applyUpserted(updated)
        return updated
    }

    func deleteTemplate(templateId: String) async throws {
        try await centralAPI.deleteTemplate(userToken: try userToken(), templateId: templateId)
        await applyDeleted(templateId: templateId)
    }

    private func userToken() throws -> String {
        guard let token = userIdentityStore.getUserAccessToken() else {
            throw APIError(code: -1, errorBody: "Not signed in")
        }
        return token
    }
}
