import SwiftUI

/// The two web chip families plus the filled badge.
struct PixelChip: View {
    enum Style {
        /// `.session-attention` / `.git-chip`: chrome text, `surface` fill,
        /// tone-coloured text and ring.
        case status(PixelTone)
        /// `.chip`: a verbatim label (CLI flags), body font, `bg2` + `rule`
        /// ring; checked → `surface` + `brand2`.
        case verbatim(checked: Bool)
        /// `.device-current` / count badges: `brand` fill, white chrome text.
        case filled
    }

    let text: String
    let style: Style

    @Environment(\.mobaideColors) private var colors

    init(_ text: String, _ style: Style) {
        self.text = text
        self.style = style
    }

    var body: some View {
        switch style {
        case .status(let tone):
            Text(text)
                .chromeText()
                .foregroundStyle(colors.color(tone))
                .padding(EdgeInsets(top: 3, leading: 8, bottom: 3, trailing: 8))
                .background(colors.surface)
                .pixelRing(colors.ring(tone))
        case .verbatim(let checked):
            Text(text)
                .font(.footnote)
                .foregroundStyle(colors.text)
                .padding(EdgeInsets(top: 4, leading: 10, bottom: 4, trailing: 10))
                .background(checked ? colors.surface : colors.bg2)
                .pixelRing(checked ? MobaideColorScheme.brand2 : colors.rule)
        case .filled:
            Text(text)
                .chromeText()
                .foregroundStyle(.white)
                .padding(EdgeInsets(top: 3, leading: 8, bottom: 3, trailing: 8))
                .background(MobaideColorScheme.brand)
                .pixelRing(MobaideColorScheme.brand2)
        }
    }
}

/// The 8×8 status square (`.session-dot`) — never a circle.
struct PixelStatusSquare: View {
    let tone: PixelTone
    var size: CGFloat = PixelMetrics.statusSquare

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Rectangle()
            .fill(tone == .muted ? colors.textMuted : colors.color(tone))
            .frame(width: size, height: size)
    }
}
