import SwiftUI

/// Labels and filtering for the New Session base-branch picker.
enum WorktreeBaseDisplay {
    static let defaultLabel = "Default branch"

    static func label(_ ref: String?) -> String { ref ?? defaultLabel }

    /// A case-insensitive substring match on the ref name.
    static func filter(_ refs: [WorktreeBaseRef], query: String) -> [WorktreeBaseRef] {
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !needle.isEmpty else { return refs }
        return refs.filter { $0.ref.lowercased().contains(needle) }
    }

    /// "3h ago" from the tip commit's ISO-8601 date; nil when absent or unreadable.
    static func relativeTime(_ iso: String?) -> String? {
        guard let date = CountdownView.parse(iso) else { return nil }
        let text = AutoScanDisplay.relativeTime(date.timeIntervalSince1970)
        return text.isEmpty ? nil : text
    }
}

/// Searchable list of the project's local and remote-tracking branches. The
/// top row (outside a search) picks nil: the agent's own default start point.
struct WorktreeBasePickerSheet: View {
    let bases: WorktreeBasesResponse
    let selected: String?
    let onPick: (String?) -> Void

    @State private var query = ""
    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors

    var body: some View {
        let refs = WorktreeBaseDisplay.filter(bases.refs, query: query)
        let searching = !query.trimmingCharacters(in: .whitespaces).isEmpty
        NavigationStack {
            List {
                if !searching {
                    row(
                        title: WorktreeBaseDisplay.defaultLabel,
                        caption: "Let the agent choose the start point",
                        ref: nil
                    )
                }
                ForEach(refs, id: \.ref) { base in
                    row(
                        title: base.ref,
                        caption: WorktreeBaseDisplay.relativeTime(base.committed_at),
                        ref: base.ref,
                        isDefault: base.ref == bases.default,
                        isLocal: base.isLocal
                    )
                }
            }
            .pixelList()
            .searchable(
                text: $query,
                placement: .navigationBarDrawer(displayMode: .always),
                prompt: "Search branches"
            )
            .overlay {
                if searching && refs.isEmpty {
                    ContentUnavailableView.search(text: query)
                }
            }
            .navigationTitle("Base branch")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .pixelSheet()
    }

    private func row(
        title: String,
        caption: String?,
        ref: String?,
        isDefault: Bool = false,
        isLocal: Bool = false
    ) -> some View {
        Button {
            onPick(ref)
            dismiss()
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.body.weight(.semibold).monospaced())
                        .foregroundStyle(colors.text)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if isDefault || isLocal || caption != nil {
                        HStack(spacing: 6) {
                            if isDefault { PixelChip("default", .status(.accent)) }
                            if isLocal { PixelChip("local", .status(.muted)) }
                            if let caption {
                                Text(caption)
                                    .font(.caption)
                                    .foregroundStyle(colors.textMuted)
                                    .lineLimit(1)
                            }
                        }
                    }
                }
                Spacer()
                if ref == selected {
                    Image(systemName: "checkmark")
                        .foregroundStyle(colors.accent)
                        .accessibilityLabel("Selected")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .pixelCard(selected: ref == selected)
        .pixelListRow()
    }
}
