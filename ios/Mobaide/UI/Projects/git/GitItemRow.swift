import SwiftUI

/// One git branch/worktree row (Android `GitItemRow`): a selection glyph in
/// selection mode, the primary/secondary labels, and status chips.
struct GitItemRow: View {
    let item: GitItem
    let inSelectionMode: Bool
    let isSelected: Bool

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 12) {
            if inSelectionMode && item.isDeletable {
                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isSelected ? colors.primary : colors.onSurfaceVariant)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(GitItemDisplay.primaryLabel(item))
                    .font(.body)
                if let secondary = GitItemDisplay.secondaryLabel(item) {
                    Text(secondary)
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                let chips = GitItemDisplay.chips(item)
                if !chips.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(Array(chips.enumerated()), id: \.offset) { pair in
                                GitChip(text: pair.element.text, style: pair.element.style)
                            }
                        }
                    }
                }
            }
            Spacer()
        }
        .padding(.vertical, 4)
    }
}

private struct GitChip: View {
    let text: String
    let style: GitItemDisplay.ChipStyle

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Text(text)
            .font(.caption2)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(foreground)
            .background(background, in: RoundedRectangle(cornerRadius: 6))
    }

    private var background: Color {
        switch style {
        case .highlight: return colors.primary.opacity(0.2)
        case .danger: return colors.errorContainer
        case .neutral, .muted: return colors.surfaceVariant
        }
    }

    private var foreground: Color {
        switch style {
        case .highlight: return colors.primary
        case .danger: return colors.error
        case .neutral: return colors.onSurfaceVariant
        case .muted: return colors.onSurfaceVariant.opacity(0.7)
        }
    }
}
