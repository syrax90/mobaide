import SwiftUI

/// Hint under every host-path field while it is empty.
let pathBrowseHint = "Type / or ~ to browse folders on the agent"

/// Folder suggestions listed in flow under a host-path field — not a popover,
/// so nothing competes with the keyboard for focus. Picking one writes
/// `<dir>/<name>/`, so the next level's folders appear straight away.
struct PathSuggestionList: View {
    let model: PathSuggestionsModel
    let onPick: (String) -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        if !model.names.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(model.names, id: \.self) { name in
                    Button {
                        onPick(name)
                    } label: {
                        Label {
                            Text("\(name)/")
                                .font(.footnote.monospaced())
                                .lineLimit(1)
                                .truncationMode(.middle)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        } icon: {
                            Image(systemName: "folder")
                                .foregroundStyle(colors.textMuted)
                        }
                        .frame(minHeight: 36)
                        .contentShape(Rectangle())
                    }
                    // A `.plain`/default button in a List row swallows the whole
                    // row's taps; borderless keeps each suggestion its own target.
                    .buttonStyle(.borderless)
                    .tint(colors.text)
                    .accessibilityLabel("Folder \(name)")
                }
                if model.overflow > 0 {
                    Text("+\(model.overflow) more — keep typing")
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                        .padding(.top, 4)
                }
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Folder suggestions")
        }
    }
}
