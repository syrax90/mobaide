import Foundation
import Observation

/// Editor target for the create/edit sheet — `create` or `edit(existing)`.
enum TemplateEditorTarget: Identifiable, Equatable {
    case create
    case edit(TemplateDto)

    var id: String {
        switch self {
        case .create: return "__create__"
        case .edit(let template): return template.template_id
        }
    }

    var template: TemplateDto? {
        if case .edit(let template) = self { return template }
        return nil
    }
}

/// Prompt-template list + CRUD (Android `TemplatesViewModel`). The list is
/// push-driven via `TemplatesRepository` (REST + `template.*` bus events);
/// favorites/all grouping and case-insensitive name sort are computed here.
/// Mutations call REST and reconcile through the shared cache.
@MainActor
@Observable
final class TemplatesModel {
    private(set) var templates: [TemplateDto] = []
    private(set) var isLoading = false
    private(set) var isSavingTemplate = false
    var editorTarget: TemplateEditorTarget?
    var deleteTarget: TemplateDto?
    var message: String?

    private let repository: TemplatesRepository

    init(repository: TemplatesRepository) {
        self.repository = repository
        self.templates = repository.currentTemplates
    }

    var favorites: [TemplateDto] {
        templates.filter { $0.is_favorite }.sorted { $0.name.lowercased() < $1.name.lowercased() }
    }

    var others: [TemplateDto] {
        templates.filter { !$0.is_favorite }.sorted { $0.name.lowercased() < $1.name.lowercased() }
    }

    var isEmpty: Bool { templates.isEmpty && !isLoading }

    func observeTemplates() async {
        for await list in repository.templates() {
            templates = list
        }
    }

    func refresh() async {
        isLoading = true
        defer { isLoading = false }
        do {
            try await repository.refreshTemplates()
        } catch {
            message = "Failed to load templates: \(ErrorText.message(for: error))"
        }
    }

    func startCreate() { editorTarget = .create }

    func startEdit(_ template: TemplateDto) { editorTarget = .edit(template) }

    func saveFromEditor(name: String, body: String) {
        // Keep the editor (and the user's draft) until the save succeeds; a
        // failed save must not discard an in-progress template body. Guard
        // against a double-tap issuing two saves.
        guard !isSavingTemplate, let target = editorTarget else { return }
        isSavingTemplate = true
        Task { [weak self] in
            guard let self else { return }
            defer { self.isSavingTemplate = false }
            do {
                switch target {
                case .edit(let template):
                    _ = try await self.repository.updateTemplate(
                        templateId: template.template_id,
                        update: UpdateTemplateRequest(name: name, body: body)
                    )
                case .create:
                    _ = try await self.repository.createTemplate(name: name, body: body)
                }
                self.editorTarget = nil  // dismiss only on success
            } catch {
                self.message = "Failed to save template: \(ErrorText.message(for: error))"
            }
        }
    }

    func toggleFavorite(_ template: TemplateDto) {
        Task { [weak self] in
            guard let self else { return }
            do {
                _ = try await self.repository.updateTemplate(
                    templateId: template.template_id,
                    update: UpdateTemplateRequest(is_favorite: !template.is_favorite)
                )
            } catch {
                self.message = "Failed to update template: \(ErrorText.message(for: error))"
            }
        }
    }

    func confirmDelete(_ template: TemplateDto) {
        deleteTarget = nil
        Task { [weak self] in
            guard let self else { return }
            do {
                try await self.repository.deleteTemplate(templateId: template.template_id)
            } catch {
                self.message = "Failed to delete template: \(ErrorText.message(for: error))"
            }
        }
    }

    func clearMessage() { message = nil }
}
