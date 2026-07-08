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
                options: sensitiveOptions()
            )
        } else {
            UIPasteboard.general.string = text
        }
    }

    /// Options for a one-time-secret copy: local-only (no Universal Clipboard
    /// leak) and auto-expiring after 60 s. Pure so the hygiene invariant is
    /// unit-testable without touching the shared pasteboard.
    static func sensitiveOptions(now: Date = Date()) -> [UIPasteboard.OptionsKey: Any] {
        [
            .localOnly: true,
            .expirationDate: now.addingTimeInterval(60),
        ]
    }
}
