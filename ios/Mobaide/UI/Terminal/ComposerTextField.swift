import SwiftUI

/// On iOS 18+ it keeps `TerminalSessionModel.composerSelection` current and
/// applies `composerCursorRequest`, so a dictated transcript lands at the
/// cursor and the cursor follows it. iOS 17 SwiftUI exposes no selection, so
/// there a transcript is appended.
struct ComposerTextField: View {
    enum Style {
        case inline
        case editor
    }

    @Bindable var model: TerminalSessionModel
    let style: Style
    var focus: FocusState<Bool>.Binding

    var body: some View {
        if #available(iOS 18.0, *) {
            SelectionTrackingComposer(model: model, style: style, focus: focus)
        } else {
            switch style {
            case .inline:
                TextField("Message", text: $model.inputText, axis: .vertical)
                    .focused(focus)
            case .editor:
                TextEditor(text: $model.inputText)
                    .focused(focus)
            }
        }
    }
}

@available(iOS 18.0, *)
private struct SelectionTrackingComposer: View {
    @Bindable var model: TerminalSessionModel
    let style: ComposerTextField.Style
    var focus: FocusState<Bool>.Binding
    @State private var selection: TextSelection?

    var body: some View {
        field
            .focused(focus)
            .onChange(of: selection) { _, next in
                model.composerSelection = Self.offsets(next, in: model.inputText)
            }
            .onChange(of: model.composerCursorRequest) { _, _ in
                let offset = model.composerSelection?.upperBound ?? model.inputText.count
                selection = Self.selection(at: offset, in: model.inputText)
            }
    }

    @ViewBuilder
    private var field: some View {
        switch style {
        case .inline:
            TextField("Message", text: $model.inputText, selection: $selection, axis: .vertical)
        case .editor:
            TextEditor(text: $model.inputText, selection: $selection)
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
