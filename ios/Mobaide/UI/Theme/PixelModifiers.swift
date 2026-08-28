import SwiftUI

/// The pixel grammar: a "border" is a 2pt inset ring, an elevation is a hard
/// 4pt offset block with no blur, corners are never rounded.
extension View {
    func pixelRing(_ color: Color, width: CGFloat = PixelMetrics.ring) -> some View {
        overlay(Rectangle().strokeBorder(color, lineWidth: width))
    }

    /// Reserves the 4pt the block occupies so siblings never overlap it;
    /// `visible: false` keeps the layout and hides the block (pressed state).
    /// Drawn as the L outside the box, never under it: a ghost button's fill is
    /// clear, and a full offset rectangle would show through as a dark body.
    func pixelShadow(_ color: Color, visible: Bool = true) -> some View {
        background(
            PixelShadowShape()
                .fill(color)
                .opacity(visible ? 1 : 0)
        )
        .padding([.trailing, .bottom], PixelMetrics.px)
    }

    func pixelPlate(fill: Color, ring: Color, insets: EdgeInsets) -> some View {
        padding(insets).background(fill).pixelRing(ring)
    }

    /// `bg2` plate with a `surface2` ring — list card.
    func pixelCard(insets: EdgeInsets = PixelMetrics.cardInsets) -> some View {
        modifier(PixelPlateModifier(kind: .card, insets: insets))
    }

    /// The same plate at panel padding — auth card, settings card.
    func pixelPanel(insets: EdgeInsets = PixelMetrics.panelInsets) -> some View {
        modifier(PixelPlateModifier(kind: .card, insets: insets))
    }

    /// `surface` fill with a `brand2` ring — the selected row.
    func pixelSelectedCard(insets: EdgeInsets = PixelMetrics.cardInsets) -> some View {
        modifier(PixelPlateModifier(kind: .selected, insets: insets))
    }

    /// A card that takes the selected plate while `selected` holds.
    func pixelCard(selected: Bool, insets: EdgeInsets = PixelMetrics.cardInsets) -> some View {
        modifier(PixelPlateModifier(kind: selected ? .selected : .card, insets: insets))
    }

    /// `surface` fill with a `danger` ring — danger zone, grace banner.
    func pixelDangerPanel(insets: EdgeInsets = PixelMetrics.bannerInsets) -> some View {
        modifier(PixelPlateModifier(kind: .danger, insets: insets))
    }

    /// `surface` fill with a `rule` ring — informational banner.
    func pixelInfoBanner(insets: EdgeInsets = PixelMetrics.bannerInsets) -> some View {
        modifier(PixelPlateModifier(kind: .info, insets: insets))
    }

    /// `surface` fill with a `surface2` ring — code block, mono value.
    func pixelCodePlate(insets: EdgeInsets = EdgeInsets(top: 8, leading: 10, bottom: 8, trailing: 10)) -> some View {
        modifier(PixelPlateModifier(kind: .code, insets: insets))
    }

    /// Departure Mono, uppercase, +0.08em — nav, labels, chips, headings.
    func chromeText(_ font: Font = MobaideTypography.chromeSmall, size: CGFloat = 11) -> some View {
        self.font(font)
            .textCase(.uppercase)
            .kerning(size * PixelMetrics.chromeTracking)
    }

    /// Chrome text in `textMuted` — the section heading recipe.
    func sectionHeading() -> some View {
        modifier(SectionHeadingModifier())
    }

    /// 22pt Departure Mono in `accent`, mixed case — the web `h1`.
    func pageTitle() -> some View {
        modifier(PageTitleModifier())
    }

    func pixelScreenBackground() -> some View {
        background(DotGridBackground())
    }

    /// Apply to a sheet's root view: square top corners on the page background.
    func pixelSheet() -> some View {
        modifier(PixelSheetModifier())
    }

    /// Apply to a `List` whose rows are pixel cards.
    func pixelList() -> some View {
        listStyle(.plain)
            .scrollContentBackground(.hidden)
            .pixelScreenBackground()
    }

    /// Apply to the rows (or their `ForEach`) of a `pixelList()`.
    func pixelListRow(insets: EdgeInsets = PixelMetrics.listRowInsets) -> some View {
        listRowBackground(Color.clear)
            .listRowSeparator(.hidden)
            .listRowInsets(insets)
    }

    /// Apply to a form-shaped `List` (not a `Form`: iOS 26 rounds every
    /// `Form` regardless of list style): square full-width rows on the page
    /// background. Sections still need `.pixelFormSection()` for their row plate.
    func pixelForm() -> some View {
        listStyle(.plain)
            .scrollContentBackground(.hidden)
            .listSectionSpacing(16)
            .pixelScreenBackground()
    }

    /// Apply to each `Section` of a `pixelForm()`.
    func pixelFormSection() -> some View {
        modifier(PixelFormSectionModifier())
    }
}

enum PixelPlateKind {
    case card, selected, danger, info, code
}

private struct PixelPlateModifier: ViewModifier {
    let kind: PixelPlateKind
    let insets: EdgeInsets

    @Environment(\.mobaideColors) private var colors

    func body(content: Content) -> some View {
        content
            .padding(insets)
            .background(fill)
            .pixelRing(ring)
    }

    private var fill: Color {
        switch kind {
        case .card: return colors.bg2
        case .selected, .danger, .info, .code: return colors.surface
        }
    }

    private var ring: Color {
        switch kind {
        case .card, .code: return colors.surface2
        case .selected: return MobaideColorScheme.brand2
        case .danger: return colors.danger
        case .info: return colors.rule
        }
    }
}

private struct SectionHeadingModifier: ViewModifier {
    @Environment(\.mobaideColors) private var colors

    func body(content: Content) -> some View {
        content
            .chromeText()
            .foregroundStyle(colors.textMuted)
    }
}

private struct PageTitleModifier: ViewModifier {
    @Environment(\.mobaideColors) private var colors

    func body(content: Content) -> some View {
        content
            .font(MobaideTypography.pageTitle)
            .foregroundStyle(colors.accent)
    }
}

private struct PixelSheetModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .presentationBackground { DotGridBackground() }
            .presentationCornerRadius(0)
    }
}

private struct PixelFormSectionModifier: ViewModifier {
    @Environment(\.mobaideColors) private var colors

    func body(content: Content) -> some View {
        content
            .listRowBackground(colors.bg2)
            .listRowSeparatorTint(colors.surface2)
    }
}

/// The visible part of a hard `4px 4px 0 0` shadow: the strip below and the
/// strip to the right of the box it belongs to (its `rect`).
struct PixelShadowShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let px = PixelMetrics.px
        path.addRect(CGRect(x: rect.minX + px, y: rect.maxY, width: rect.width, height: px))
        path.addRect(CGRect(x: rect.maxX, y: rect.minY + px, width: px, height: rect.height))
        return path
    }
}

/// The section heading recipe as a `Section` header view.
struct PixelSectionHeader: View {
    let title: String

    init(_ title: String) {
        self.title = title
    }

    var body: some View {
        Text(title).sectionHeading()
    }
}

/// A section footer for a `pixelForm()` list: a plain list renders footers as
/// ordinary rows, so the hint recipe is applied here.
struct PixelSectionFooter: View {
    let text: String
    var tone: PixelTone? = nil

    @Environment(\.mobaideColors) private var colors

    init(_ text: String, tone: PixelTone? = nil) {
        self.text = text
        self.tone = tone
    }

    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(tone.map(colors.color) ?? colors.textMuted)
    }
}

/// A 2pt `surface2` rule — the system has no hairlines.
struct PixelDivider: View {
    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Rectangle()
            .fill(colors.surface2)
            .frame(height: PixelMetrics.ring)
    }
}

/// 8×8 `brand` square with a 3pt `brand2` hard shadow — the link bullet.
struct PixelLinkBullet: View {
    var body: some View {
        Rectangle()
            .fill(MobaideColorScheme.brand)
            .frame(width: PixelMetrics.statusSquare, height: PixelMetrics.statusSquare)
            .background(
                Rectangle()
                    .fill(MobaideColorScheme.brand2)
                    .offset(x: 3, y: 3)
            )
            .padding([.trailing, .bottom], 3)
    }
}
