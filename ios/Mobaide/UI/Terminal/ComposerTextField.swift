import SwiftUI

/// On iOS 18+ it keeps the owner's selection (character offsets) current and
/// applies its cursor requests, so a dictated transcript lands at the cursor
/// and the cursor follows it. iOS 17 SwiftUI exposes no selection, so there a
/// transcript is appended. Shared by the terminal and chat composers.
struct ComposerTextField: View {
    enum Style {
        case inline
        case editor
    }

    static let defaultPlaceholder = "Message"

    @Binding var text: String
    @Binding var selection: Range<Int>?
    let cursorRequest: Int
    let style: Style
    var placeholder = Self.defaultPlaceholder
    var focus: FocusState<Bool>.Binding

    init(
        text: Binding<String>,
        selection: Binding<Range<Int>?>,
        cursorRequest: Int,
        style: Style,
        placeholder: String = Self.defaultPlaceholder,
        focus: FocusState<Bool>.Binding
    ) {
        _text = text
        _selection = selection
        self.cursorRequest = cursorRequest
        self.style = style
        self.placeholder = placeholder
        self.focus = focus
    }

    init(model: TerminalSessionModel, style: Style, focus: FocusState<Bool>.Binding) {
        self.init(
            text: Binding(get: { model.inputText }, set: { model.inputText = $0 }),
            selection: Binding(get: { model.composerSelection }, set: { model.composerSelection = $0 }),
            cursorRequest: model.composerCursorRequest,
            style: style,
            focus: focus
        )
    }

    var body: some View {
        if #available(iOS 18.0, *) {
            SelectionTrackingComposer(
                text: $text,
                offsets: $selection,
                cursorRequest: cursorRequest,
                style: style,
                placeholder: placeholder,
                focus: focus
            )
        } else {
            switch style {
            case .inline:
                TextField(placeholder, text: $text, axis: .vertical)
                    .focused(focus)
            case .editor:
                TextEditor(text: $text)
                    .focused(focus)
            }
        }
    }
}

@available(iOS 18.0, *)
private struct SelectionTrackingComposer: View {
    @Binding var text: String
    @Binding var offsets: Range<Int>?
    let cursorRequest: Int
    let style: ComposerTextField.Style
    let placeholder: String
    var focus: FocusState<Bool>.Binding
    @State private var selection: TextSelection?

    var body: some View {
        field
            .focused(focus)
            .onChange(of: selection) { _, next in
                offsets = Self.offsets(next, in: text)
            }
            .onChange(of: cursorRequest) { _, _ in
                let offset = offsets?.upperBound ?? text.count
                selection = Self.selection(at: offset, in: text)
            }
    }

    @ViewBuilder
    private var field: some View {
        switch style {
        case .inline:
            TextField(placeholder, text: $text, selection: $selection, axis: .vertical)
        case .editor:
            TextEditor(text: $text, selection: $selection)
        }
    }

    /// Character offsets of a single selection; nil for none, a
    /// multi-selection, or indices that no longer fit the text.
    private static func offsets(_ selection: TextSelection?, in text: String) -> Range<Int>? {
        guard let selection, case .selection(let range) = selection.indices else { return nil }
        guard range.upperBound <= text.endIndex else { return nil }
        let lower = text.distance(from: text.startIndex, to: range.lowerBound)
        let upper = text.distance(from: text.startIndex, to: range.upperBound)
        return lower..<max(lower, upper)
    }

    private static func selection(at offset: Int, in text: String) -> TextSelection {
        let index = text.index(text.startIndex, offsetBy: min(max(offset, 0), text.count))
        return TextSelection(insertionPoint: index)
    }
}
