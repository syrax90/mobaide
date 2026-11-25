import SwiftUI
import UIKit

/// Read-only text the user can select like any iOS text: a long press picks a
/// word, the handles extend it, and the edit menu copies just that part
/// (SwiftUI's `.textSelection(.enabled)` copies the whole `Text`). Wrapped
/// lines hug the longest one; `wraps: false` keeps each line whole for a
/// sideways scroll.
struct SelectableText: View {
    let text: AttributedString
    var style: UIFont.TextStyle = .body
    var weight: UIFont.Weight = .regular
    var monospaced = false
    /// A point size that does not follow Dynamic Type (code).
    var pointSize: CGFloat?
    var color: Color
    var strikethrough = false
    var wraps = true

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.mobaideColors) private var colors

    init(
        _ text: String,
        style: UIFont.TextStyle = .body,
        weight: UIFont.Weight = .regular,
        monospaced: Bool = false,
        pointSize: CGFloat? = nil,
        color: Color,
        strikethrough: Bool = false,
        wraps: Bool = true
    ) {
        self.init(
            markdown: AttributedString(text), style: style, weight: weight, monospaced: monospaced,
            pointSize: pointSize, color: color, strikethrough: strikethrough, wraps: wraps
        )
    }

    /// `text` carries inline Markdown intents (bold, italic, code, strikethrough, links).
    init(
        markdown text: AttributedString,
        style: UIFont.TextStyle = .body,
        weight: UIFont.Weight = .regular,
        monospaced: Bool = false,
        pointSize: CGFloat? = nil,
        color: Color,
        strikethrough: Bool = false,
        wraps: Bool = true
    ) {
        self.text = text
        self.style = style
        self.weight = weight
        self.monospaced = monospaced
        self.pointSize = pointSize
        self.color = color
        self.strikethrough = strikethrough
        self.wraps = wraps
    }

    var body: some View {
        let spec = SelectableTextSpec(
            text: text, style: style, weight: weight, monospaced: monospaced, pointSize: pointSize,
            color: color, strikethrough: strikethrough, sizeCategory: UIContentSizeCategory(dynamicTypeSize),
            strikeColor: colors.textMuted
        )
        let font = spec.baseFont
        // A representable has no baseline of its own; without these guides a
        // `.firstTextBaseline` stack (list markers) aligns to its bottom edge.
        SelectableTextView(spec: spec, wraps: wraps, accent: UIColor(colors.accent))
            .alignmentGuide(.firstTextBaseline) { _ in font.ascender }
            .alignmentGuide(.lastTextBaseline) { d in d.height + font.descender }
    }
}

struct SelectableTextSpec: Equatable {
    let text: AttributedString
    let style: UIFont.TextStyle
    let weight: UIFont.Weight
    let monospaced: Bool
    let pointSize: CGFloat?
    let color: Color
    let strikethrough: Bool
    let sizeCategory: UIContentSizeCategory
    let strikeColor: Color

    var resolvedPointSize: CGFloat {
        pointSize ?? UIFont.preferredFont(
            forTextStyle: style,
            compatibleWith: UITraitCollection(preferredContentSizeCategory: sizeCategory)
        ).pointSize
    }

    var baseFont: UIFont {
        Self.font(size: resolvedPointSize, weight: weight, italic: false, monospaced: monospaced)
    }

    /// Inline intents become UIKit attributes: SwiftUI's own attribute scope
    /// does not survive the bridge to `NSAttributedString`.
    func attributed() -> NSAttributedString {
        let size = resolvedPointSize
        let foreground = UIColor(color)
        let strike = UIColor(strikeColor)
        let result = NSMutableAttributedString()
        for run in text.runs {
            let intent = run.inlinePresentationIntent ?? []
            let isCode = intent.contains(.code)
            var attributes: [NSAttributedString.Key: Any] = [
                .font: Self.font(
                    size: size,
                    weight: intent.contains(.stronglyEmphasized) ? .bold : weight,
                    italic: intent.contains(.emphasized),
                    monospaced: monospaced || isCode
                ),
                .foregroundColor: foreground,
            ]
            if !monospaced, let fill = run[AttributeScopes.SwiftUIAttributes.BackgroundColorAttribute.self] {
                attributes[.backgroundColor] = UIColor(fill)
            }
            if strikethrough || intent.contains(.strikethrough) {
                attributes[.strikethroughStyle] = NSUnderlineStyle.single.rawValue
                attributes[.strikethroughColor] = strikethrough ? strike : foreground
            }
            if let link = run.link {
                attributes[.link] = link
            }
            result.append(NSAttributedString(string: String(text[run.range].characters), attributes: attributes))
        }
        return result
    }

    private static func font(size: CGFloat, weight: UIFont.Weight, italic: Bool, monospaced: Bool) -> UIFont {
        let font: UIFont = monospaced
            ? .monospacedSystemFont(ofSize: size, weight: weight)
            : .systemFont(ofSize: size, weight: weight)
        guard italic,
              let descriptor = font.fontDescriptor.withSymbolicTraits(font.fontDescriptor.symbolicTraits.union(.traitItalic))
        else { return font }
        return UIFont(descriptor: descriptor, size: size)
    }
}

private struct SelectableTextView: UIViewRepresentable {
    let spec: SelectableTextSpec
    let wraps: Bool
    let accent: UIColor

    final class Coordinator {
        var spec: SelectableTextSpec?
        var measured: (width: CGFloat, size: CGSize)?
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeUIView(context: Context) -> SelectableUITextView {
        let view = SelectableUITextView()
        view.isEditable = false
        view.isSelectable = true
        view.isScrollEnabled = false
        view.backgroundColor = .clear
        view.textContainerInset = .zero
        view.textContainer.lineFragmentPadding = 0
        view.dataDetectorTypes = []
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        if !wraps {
            view.textContainer.widthTracksTextView = false
            view.textContainer.size = CGSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        }
        return view
    }

    func updateUIView(_ view: SelectableUITextView, context: Context) {
        view.tintColor = accent
        view.linkTextAttributes = [.foregroundColor: accent]
        guard context.coordinator.spec != spec else { return }
        context.coordinator.spec = spec
        context.coordinator.measured = nil
        view.attributedText = spec.attributed()
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: SelectableUITextView, context: Context) -> CGSize? {
        let limit = wraps ? (proposal.width ?? .greatestFiniteMagnitude) : .greatestFiniteMagnitude
        if let measured = context.coordinator.measured, measured.width == limit {
            return measured.size
        }
        let natural = uiView.attributedText.boundingRect(
            with: CGSize(width: limit, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            context: nil
        )
        // A point of slack so TextKit never wraps the longest line again.
        let width = min(ceil(natural.width) + 1, limit)
        let height = uiView.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude)).height
        let size = CGSize(width: width, height: ceil(height))
        context.coordinator.measured = (limit, size)
        return size
    }
}

/// Drops its highlight once another view takes focus, as an editable field
/// would; a read-only `UITextView` otherwise keeps a stale selection painted.
final class SelectableUITextView: UITextView {
    override func resignFirstResponder() -> Bool {
        let resigned = super.resignFirstResponder()
        if resigned, selectedRange.length > 0 {
            selectedRange = NSRange(location: 0, length: 0)
        }
        return resigned
    }
}
