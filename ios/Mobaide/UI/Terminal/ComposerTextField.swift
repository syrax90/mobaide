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

/// Text put into a composer draft at its selection (Android `insertAtCursor`).
enum ComposerInsertion {
    /// `inserted` replaces `selection` (nil: the end of `text`). With `spaced`,
    /// a space goes on either side where it would otherwise run into a word
    /// (dictated text); without it the text goes in as is (a template body).
    /// `cursor` is the offset right after the inserted text.
    static func insert(
        _ inserted: String,
        into text: String,
        selection: Range<Int>?,
        spaced: Bool
    ) -> (text: String, cursor: Int) {
        let length = text.count
        let range = selection ?? length..<length
        let lower = min(max(range.lowerBound, 0), length)
        let upper = min(max(range.upperBound, lower), length)
        let before = String(text.prefix(lower))
        let after = String(text.dropFirst(upper))
        let lead = spaced ? (before.last.map { $0.isWhitespace ? "" : " " } ?? "") : ""
        let trail = spaced ? (after.first.map { $0.isWhitespace ? "" : " " } ?? "") : ""
        let piece = lead + inserted + trail
        return (before + piece + after, before.count + piece.count)
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
