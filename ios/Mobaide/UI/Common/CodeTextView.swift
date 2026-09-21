import SwiftUI
import UIKit

/// The text behind a `CodeTextView`, held outside SwiftUI state: a file of a
/// quarter megabyte must not be copied through an `@State` string and diffed
/// on every keystroke. Owners read `text` when they need it and are told of
/// edits through `onEdit`, without a payload. Offsets are UTF-16, the unit
/// `UITextView` selects in.
@MainActor
final class CodeTextBuffer {
    fileprivate weak var view: UITextView?
    private var stored = ""

    /// Called after every user edit.
    var onEdit: (() -> Void)?

    var text: String {
        get { view?.text ?? stored }
        set {
            stored = newValue
            guard let view else { return }
            view.text = newValue
            view.selectedRange = NSRange(location: 0, length: 0)
            view.setContentOffset(.zero, animated: false)
        }
    }

    var caretOffset: Int { view?.selectedRange.location ?? 0 }

    /// Select `range` and bring it into view, raising the keyboard if needed.
    func select(_ range: NSRange) {
        guard let view else { return }
        view.becomeFirstResponder()
        view.selectedRange = range
        view.scrollRangeToVisible(range)
    }

    fileprivate func attach(_ view: UITextView) {
        self.view = view
        view.text = stored
    }

    fileprivate func userEdited() {
        onEdit?()
    }
}

/// A plain-text source editor. `UITextView` rather than SwiftUI's `TextEditor`
/// for two things the latter cannot do on iOS 17: smart quotes and dashes have
/// no SwiftUI modifier and silently rewrite code, and there is no way to move
/// the selection or scroll to it.
struct CodeTextView: UIViewRepresentable {
    let buffer: CodeTextBuffer
    var isEditable = true
    var textColor: UIColor = .label
    var accessibilityIdentifier: String?

    func makeCoordinator() -> Coordinator {
        Coordinator(buffer: buffer)
    }

    func makeUIView(context: Context) -> UITextView {
        let view = UITextView()
        view.delegate = context.coordinator
        view.font = MobaideTypography.codeUIFont
        view.backgroundColor = .clear
        view.alwaysBounceVertical = true
        view.keyboardDismissMode = .interactive
        view.textContainerInset = UIEdgeInsets(top: 8, left: 8, bottom: 8, right: 8)
        view.autocorrectionType = .no
        view.autocapitalizationType = .none
        view.spellCheckingType = .no
        view.smartQuotesType = .no
        view.smartDashesType = .no
        view.smartInsertDeleteType = .no
        view.accessibilityIdentifier = accessibilityIdentifier
        buffer.attach(view)
        return view
    }

    func updateUIView(_ view: UITextView, context: Context) {
        if view.isEditable != isEditable { view.isEditable = isEditable }
        if view.textColor != textColor { view.textColor = textColor }
    }

    @MainActor
    final class Coordinator: NSObject, UITextViewDelegate {
        private let buffer: CodeTextBuffer

        init(buffer: CodeTextBuffer) {
            self.buffer = buffer
        }

        func textViewDidChange(_ textView: UITextView) {
            buffer.userEdited()
        }
    }
}
