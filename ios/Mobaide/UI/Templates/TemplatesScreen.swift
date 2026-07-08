import SwiftUI

/// Prompt templates (Android `TemplatesScreen`): Favorites / All grouping,
/// favorite toggle, create/edit sheet, and delete confirmation. Live-synced
/// via `TemplatesRepository` + the user-events bus.
struct TemplatesScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            TemplatesContent(container: container)
        }
    }
}

private struct TemplatesContent: View {
    @State private var model: TemplatesModel
    @State private var toast: ToastMessage?

    init(container: AppContainer) {
        _model = State(initialValue: TemplatesModel(repository: container.templatesRepository))
    }

    var body: some View {
        content
            .navigationTitle("Prompt templates")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { model.startCreate() } label: { Image(systemName: "plus") }
                }
            }
            .refreshable { await model.refresh() }
            .task { await model.observeTemplates() }
            .task { await model.refresh() }
            .sheet(item: $model.editorTarget) { target in
                CreateOrEditTemplateSheet(target: target.template) { name, body in
                    model.saveFromEditor(name: name, body: body)
                }
            }
            .alert(
                "Delete template?",
                isPresented: Binding(
                    get: { model.deleteTarget != nil },
                    set: { if !$0 { model.deleteTarget = nil } }
                ),
                presenting: model.deleteTarget
            ) { template in
                Button("Delete", role: .destructive) { model.confirmDelete(template) }
                Button("Cancel", role: .cancel) { model.deleteTarget = nil }
            } message: { template in
                Text("Delete “\(template.name)”? This cannot be undone.")
            }
            .toast($toast)
            .onChange(of: model.message) { _, newValue in
                if let newValue {
                    toast = ToastMessage(text: newValue)
                    model.clearMessage()
                }
            }
    }

    @ViewBuilder
    private var content: some View {
        if model.isEmpty {
            if model.isLoading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView(
                    "No templates yet",
                    systemImage: "doc.plaintext",
                    description: Text("Tap + to create one.")
                )
            }
        } else {
            List {
                if !model.favorites.isEmpty {
                    Section("Favorites") {
                        ForEach(model.favorites, id: \.template_id) { row($0) }
                    }
                }
                if !model.others.isEmpty {
                    Section("All") {
                        ForEach(model.others, id: \.template_id) { row($0) }
                    }
                }
            }
            .listStyle(.insetGrouped)
        }
    }

    private func row(_ template: TemplateDto) -> some View {
        TemplateRow(
            template: template,
            onToggleFavorite: { model.toggleFavorite(template) },
            onEdit: { model.startEdit(template) },
            onDelete: { model.deleteTarget = template }
        )
    }
}

private struct TemplateRow: View {
    let template: TemplateDto
    let onToggleFavorite: () -> Void
    let onEdit: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 4) {
                Text(template.name).font(.headline).lineLimit(1)
                Text(template.body)
                    .font(MobaideTypography.code)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
            Button(action: onToggleFavorite) {
                Image(systemName: template.is_favorite ? "star.fill" : "star")
                    .foregroundStyle(template.is_favorite ? Color.yellow : Color.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(template.is_favorite ? "Unfavorite" : "Favorite")
            Menu {
                Button { onEdit() } label: { Label("Edit", systemImage: "pencil") }
                Button(role: .destructive) { onDelete() } label: { Label("Delete", systemImage: "trash") }
            } label: {
                Image(systemName: "ellipsis")
                    .padding(8)
                    .contentShape(Rectangle())
            }
        }
        .padding(.vertical, 4)
    }
}
