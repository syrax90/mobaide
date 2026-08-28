import SwiftUI

/// One git branch/worktree row (Android `GitItemRow`): the primary/secondary
/// labels and status chips. Selection is rendered by the enclosing `List`.
struct GitItemRow: View {
    let item: GitItem

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(GitItemDisplay.primaryLabel(item))
                    .font(.body.weight(.semibold))
                    .foregroundStyle(colors.text)
                if let secondary = GitItemDisplay.secondaryLabel(item) {
                    Text(secondary)
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                let chips = GitItemDisplay.chips(item)
                if !chips.isEmpty {
                    // A horizontal ScrollView here would install its own pan
                    // recognizer and swallow the row's trailing swipe.
                    HStack(spacing: 6) {
                        ForEach(Array(chips.enumerated()), id: \.offset) { pair in
                            GitChip(text: pair.element.text, style: pair.element.style)
                        }
                    }
                }
            }
            Spacer()
        }
    }
}

/// Web `.git-chip` tones: highlight → accent, danger → danger, neutral →
/// muted status chip, muted → a bare verbatim chip.
private struct GitChip: View {
    let text: String
    let style: GitItemDisplay.ChipStyle

    var body: some View {
        switch style {
        case .highlight: PixelChip(text, .status(.accent))
        case .danger: PixelChip(text, .status(.danger))
        case .neutral: PixelChip(text, .status(.muted))
        case .muted: PixelChip(text, .verbatim(checked: false))
        }
    }
}
