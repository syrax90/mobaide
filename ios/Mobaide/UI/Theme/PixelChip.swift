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

/// `PixelStatusSquare`'s footprint split into four cells, one lit and walking
/// clockwise with a fading trail. Hard steps, no
/// tweening. Reduce Motion leaves a still checkerboard, still unlike the plain
/// square.
struct PixelWorkingSquare: View {
    var size: CGFloat = PixelMetrics.statusSquare

    @Environment(\.mobaideColors) private var colors
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Cells as (column, row), in the clockwise order the lit cell walks.
    private static let cells: [(column: Int, row: Int)] = [(0, 0), (1, 0), (1, 1), (0, 1)]
    private static let stepSeconds: TimeInterval = 0.2

    var body: some View {
        if reduceMotion {
            grid(Self.cells.indices.map { $0 % 2 == 0 ? 1 : 0.25 })
        } else {
            TimelineView(.periodic(from: .now, by: Self.stepSeconds)) { context in
                grid(Self.opacities(at: context.date))
            }
        }
    }

    private static func opacities(at date: Date) -> [Double] {
        let count = cells.count
        let step = Int(date.timeIntervalSinceReferenceDate / stepSeconds) % count
        return cells.indices.map { index in
            switch (step - index + count) % count {
            case 0: return 1
            case 1: return 0.55
            default: return 0.25
            }
        }
    }

    private func grid(_ opacities: [Double]) -> some View {
        let color = colors.accent
        return Canvas { context, canvasSize in
            let cell = canvasSize.width / 2
            for (index, position) in Self.cells.enumerated() {
                let rect = CGRect(
                    x: CGFloat(position.column) * cell,
                    y: CGFloat(position.row) * cell,
                    width: cell,
                    height: cell
                )
                context.fill(Path(rect), with: .color(color.opacity(opacities[index])))
            }
        }
        .frame(width: size, height: size)
    }
}
