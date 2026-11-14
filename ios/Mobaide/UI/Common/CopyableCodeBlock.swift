import SwiftUI

/// Mirror of Android `CopyableCodeBlock`: chrome label over a monospace value
/// on the web `.code-block-value` plate, with a trailing copy button.
struct CopyableCodeBlock: View {
    let label: String
    let code: String
    var isSensitive: Bool = false
    var singleLine: Bool = true

    @Environment(\.mobaideColors) private var colors
    @State private var copied = false

    var body: some View {
        HStack(alignment: .center, spacing: 4) {
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                    .sectionHeading()
                codeText
                    .foregroundStyle(colors.text)
            }
            Spacer(minLength: 0)
            Button {
                Clipboard.copy(code, isSensitive: isSensitive)
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                copied = true
                AccessibilityNotification.Announcement("Copied").post()
            } label: {
                Image(systemName: copied ? "checkmark" : "doc.on.doc")
                    .font(.system(size: 16))
                    .foregroundStyle(copied ? colors.ok : colors.accent)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            // Above the value: a long one-line value is wider than its slot.
            .zIndex(1)
            .accessibilityLabel("Copy \(label)")
            .accessibilityValue(copied ? "Copied" : "")
        }
        .pixelCodePlate(insets: EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 0))
        .task(id: copied) {
            guard copied else { return }
            try? await Task.sleep(for: .seconds(1.5))
            copied = false
        }
    }

    @ViewBuilder
    private var codeText: some View {
        if singleLine {
            ScrollView(.horizontal, showsIndicators: false) {
                if isSensitive {
                    Text(code)
                        .font(MobaideTypography.code)
                        .lineLimit(1)
                        .textSelection(.disabled)
                } else {
                    Text(code)
                        .font(MobaideTypography.code)
                        .lineLimit(1)
                        .textSelection(.enabled)
                }
            }
            // The selectable text is as wide as the whole value; without the
            // clip it reaches under the copy button and takes its taps.
            .clipped()
            .contentShape(Rectangle())
        } else {
            if isSensitive {
                Text(code)
                    .font(MobaideTypography.code)
                    .textSelection(.disabled)
            } else {
                Text(code)
                    .font(MobaideTypography.code)
                    .textSelection(.enabled)
            }
        }
    }
}
