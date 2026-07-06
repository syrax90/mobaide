import UIKit
import UniformTypeIdentifiers

/// Mirror of Android `ClipboardUtil.copyToClipboard`.
enum Clipboard {
    /// `isSensitive` keeps the value local-only (no Universal Clipboard)
    /// and expires it after 60 s — the iOS analogue of Android's
    /// `EXTRA_IS_SENSITIVE` preview suppression.
    @MainActor
    static func copy(_ text: String, isSensitive: Bool = false) {
        if isSensitive {
            UIPasteboard.general.setItems(
                [[UTType.utf8PlainText.identifier: text]],
                options: [
                    .localOnly: true,
                    .expirationDate: Date().addingTimeInterval(60),
                ]
            )
        } else {
            UIPasteboard.general.string = text
        }
    }
}
