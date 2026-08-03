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

    /// Whether the pasteboard advertises pasteable text.
    ///
    /// `hasStrings` is a description-level probe that does **not** read the
    /// contents, so it never raises the iOS paste banner and is safe to call
    /// from menu-gating code. Mirror of Android `hasClipboardText()`, which
    /// checks the clip *description* for the same reason (its Android 12+
    /// access toast).
    @MainActor
    static var hasText: Bool { UIPasteboard.general.hasStrings }

    /// The pasteboard's text, or nil when it holds none.
    ///
    /// Reading the contents **does** raise the paste banner, so call this only
    /// in response to an explicit user gesture — never from gating or
    /// rendering. Android's `readClipboardText()` additionally rejects URI-only
    /// items, because coercing one opens its ContentProvider synchronously on
    /// the calling thread; `UIPasteboard.string` resolves in-process and simply
    /// returns nil without a string representation, so iOS needs no counterpart.
    @MainActor
    static func readText() -> String? {
        guard UIPasteboard.general.hasStrings else { return nil }
        guard let text = UIPasteboard.general.string, !text.isEmpty else { return nil }
        return text
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
