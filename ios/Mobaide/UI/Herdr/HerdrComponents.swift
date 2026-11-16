import SwiftUI

/// The leading mark of a Herdr row: the session list's walking square while an
/// agent works, its static square otherwise. Decorative: the chip has the words.
struct HerdrStatusMark: View {
    let status: HerdrStatus

    var body: some View {
        Group {
            if status == .working {
                PixelWorkingSquare()
            } else {
                PixelStatusSquare(tone: HerdrDisplay.markTone(status))
            }
        }
        .accessibilityHidden(true)
    }
}

/// The Herdr entry on the session list: one line that says how many agents run
/// in Herdr and whether one needs the user.
struct HerdrCard: View {
    let summary: String
    let status: HerdrStatus

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 12) {
            HerdrStatusMark(status: status)
            VStack(alignment: .leading, spacing: 3) {
                Text("Herdr workspaces")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(colors.text)
                Text(summary)
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(1)
            }
            Spacer()
            // Only a status that asks for the user: "Working" is in the summary.
            if status.needsUser, let chip = HerdrDisplay.chip(status) {
                PixelChip(chip.text, .status(chip.tone))
                    .fixedSize()
            }
            Image(systemName: "chevron.right")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(colors.textMuted)
                .accessibilityHidden(true)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("herdr-card")
    }
}
