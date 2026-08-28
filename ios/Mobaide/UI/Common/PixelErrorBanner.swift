import SwiftUI

/// Web `.auth-error` / `.page-error`: a `surface` plate ringed and lettered in
/// `danger`, occupying the row it reports on.
struct PixelErrorBanner: View {
    let text: String

    @Environment(\.mobaideColors) private var colors

    init(_ text: String) {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(colors.danger)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(PixelMetrics.bannerInsets)
            .background(colors.surface)
            .pixelRing(colors.danger)
            .accessibilityAddTraits(.isStaticText)
    }
}
