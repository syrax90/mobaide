import SwiftUI

/// The picker's grouping, ported from Android `TemplatesPickerSheet` and web
/// `templateRepository`. Deliberately not `TemplatesModel`'s grouping, which
/// answers "what do I own" and has no recents section.
enum TemplateGrouping {
    struct Grouped: Equatable {
        var recents: [TemplateDto]
        var favorites: [TemplateDto]
        var others: [TemplateDto]

        var isEmpty: Bool { recents.isEmpty && favorites.isEmpty && others.isEmpty }
    }

    static let maxRecents = 5

    /// Five most recently used first, then favourites *not already in that
    /// list*, then everything else, the last two sorted case-insensitively by
    /// name. The exclusions are what keep a row out of more than one section.
    ///
    /// Two deliberate, measured divergences, both invisible for ASCII names:
    /// the sort is `lowercased()` — ordinal, matching Android's
    /// `sortedBy { lowercase() }` and this app's own `TemplatesModel`, while
    /// web's `byName` collates with `localeCompare`, so web already orders
    /// accented names differently from both mobile clients. The filter stays
    /// `localizedCaseInsensitiveContains` because it scans every body on every
    /// keystroke and the locale-invariant forms measured 4-19x slower.
    static func group(_ all: [TemplateDto], query: String) -> Grouped {
        let needle: String = query.trimmingCharacters(in: .whitespacesAndNewlines)
        var matching: [TemplateDto] = all
        if !needle.isEmpty {
            matching = all.filter { (template: TemplateDto) -> Bool in
                template.name.localizedCaseInsensitiveContains(needle)
                    || template.body.localizedCaseInsensitiveContains(needle)
            }
        }

        let used: [TemplateDto] = matching.filter { $0.last_used_at != nil }
        let byRecency: [TemplateDto] = used.sorted { (a: TemplateDto, b: TemplateDto) -> Bool in
            (a.last_used_at ?? "") > (b.last_used_at ?? "")
        }
        let recents: [TemplateDto] = Array(byRecency.prefix(maxRecents))
        let recentIds: Set<String> = Set(recents.map { $0.template_id })

        let byName: (TemplateDto, TemplateDto) -> Bool = { a, b in
            a.name.lowercased() < b.name.lowercased()
        }
        let favorites: [TemplateDto] = matching
            .filter { $0.is_favorite && !recentIds.contains($0.template_id) }
            .sorted(by: byName)
        let favoriteIds: Set<String> = Set(favorites.map { $0.template_id })
        let others: [TemplateDto] = matching
            .filter { !recentIds.contains($0.template_id) && !favoriteIds.contains($0.template_id) }
            .sorted(by: byName)

        return Grouped(recents: recents, favorites: favorites, others: others)
    }
}

/// Grouped template picker (Recents ≤5 by `last_used_at`, Favorites, All).
/// Tapping a row inserts its body into the composer and touches it server-side
/// so recents reorder (Android `TemplatesPickerSheet` + `applyTemplate`).
struct TemplatesPickerSheet: View {
    let model: TerminalSessionModel

    @State private var templates: [TemplateDto] = []
    @State private var isLoading = true
    @State private var query = ""
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        // Bound once per pass, not a computed property: `body` reads it four
        // times, and each read would re-run the filter over every template
        // body. Android holds the same result in `remember(templates, query)`.
        let grouped = TemplateGrouping.group(templates, query: query)
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView()
                } else if templates.isEmpty {
                    ContentUnavailableView("No templates", systemImage: "doc.text")
                } else {
                    List {
                        section("Recents", grouped.recents)
                        section("Favorites", grouped.favorites)
                        section("All", grouped.others)
                    }
                    .searchable(
                        text: $query,
                        placement: .navigationBarDrawer(displayMode: .always),
                        prompt: "Search templates"
                    )
                    // Distinct from the empty-account state above: a filter that
                    // matched nothing is fixed by editing the filter.
                    .overlay {
                        if grouped.isEmpty {
                            ContentUnavailableView.search(text: query)
                        }
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
