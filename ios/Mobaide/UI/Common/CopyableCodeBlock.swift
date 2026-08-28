import SwiftUI

/// Mirror of Android `CopyableCodeBlock`: chrome label over a monospace value
/// on the web `.code-block-value` plate, with a trailing copy button.
struct CopyableCodeBlock: View {
    let label: String
    let code: String
    var isSensitive: Bool = false
    var singleLine: Bool = true

    @Environment(\.mobaideColors) private var colors

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
            } label: {
                Image(systemName: "doc.on.doc")
                    .font(.system(size: 16))
                    .foregroundStyle(colors.accent)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Copy \(label)")
        }
        .pixelCodePlate(insets: EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 0))
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
