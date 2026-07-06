import SwiftUI

/// Mirror of Android `CopyableCodeBlock`: labeled monospace value on a
/// surface-variant card with a trailing copy button.
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
                    .font(MobaideTypography.labelSmall)
                    .foregroundStyle(colors.onSurfaceVariant)
                codeText
            }
            Spacer(minLength: 0)
            Button {
                Clipboard.copy(code, isSensitive: isSensitive)
                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            } label: {
                Image(systemName: "doc.on.doc")
                    .font(.system(size: 16))
            }
            .accessibilityLabel("Copy \(label)")
        }
        .padding(EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 4))
        .background(colors.surfaceVariant, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private var codeText: some View {
        if singleLine {
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(MobaideTypography.code)
                    .lineLimit(1)
                    .textSelection(.enabled)
            }
        } else {
            Text(code)
                .font(MobaideTypography.code)
                .textSelection(.enabled)
        }
    }
}
