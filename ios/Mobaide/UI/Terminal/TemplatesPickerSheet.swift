import SwiftUI

/// Grouped template picker (Recents ≤5 by `last_used_at`, Favorites, All).
/// Tapping a row inserts its body into the composer and touches it server-side
/// so recents reorder (Android `TemplatesPickerSheet` + `applyTemplate`).
struct TemplatesPickerSheet: View {
    let model: TerminalSessionModel

    @State private var templates: [TemplateDto] = []
    @State private var isLoading = true
    @Environment(\.dismiss) private var dismiss

    private var recents: [TemplateDto] {
        templates
            .filter { $0.last_used_at != nil }
            .sorted { ($0.last_used_at ?? "") > ($1.last_used_at ?? "") }
            .prefix(5)
            .map { $0 }
    }

    private var favorites: [TemplateDto] {
        templates.filter { $0.is_favorite }
    }

    private var all: [TemplateDto] {
        templates.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView()
                } else if templates.isEmpty {
                    ContentUnavailableView("No templates", systemImage: "doc.text")
                } else {
                    List {
                        section("Recents", recents)
                        section("Favorites", favorites)
                        section("All", all)
                    }
                }
            }
            .navigationTitle("Templates")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .task {
            templates = await model.loadTemplates()
            isLoading = false
        }
    }

    @ViewBuilder
    private func section(_ title: String, _ items: [TemplateDto]) -> some View {
        if !items.isEmpty {
            Section(title) {
                ForEach(items, id: \.template_id) { template in
                    Button {
                        model.applyTemplate(template)
                        dismiss()
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(template.name).font(.body)
                                if template.is_favorite {
                                    Image(systemName: "star.fill").foregroundStyle(.yellow).font(.caption)
                                }
                            }
                            Text(template.body)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}
