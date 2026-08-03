import SwiftUI

/// Floating actions for a terminal text selection: **Send to Input**, **Copy**,
/// **Paste** — Android's item set and order (`TextSelectionCursorController`
/// `ACTION_SEND_TO_INPUT` / `ACTION_COPY` / `ACTION_PASTE`) — plus a Cancel that
/// the sticky selection mode needs as an explicit exit.
///
/// Rendered in SwiftUI over the terminal rather than as a `UIEditMenuInteraction`
/// or `UIMenuController`: both build their items from the responder chain, and
/// this terminal deliberately refuses first responder unless `keyboardGate` is
/// open, so a system menu would either come up empty or raise the software
/// keyboard. Owning the toolbar also keeps it themed with the app and testable.
struct TerminalSelectionToolbar: View {
    @Bindable var model: TerminalSessionModel
    /// The area the toolbar is placed within — the terminal host's bounds.
    let container: CGSize

    @Environment(\.mobaideColors) private var colors

    private static let estimatedSize = CGSize(width: 280, height: 44)
    private static let margin: CGFloat = 8

    var body: some View {
        if let selection = model.selectionUi {
            let position = Self.position(
                for: selection.anchor,
                container: container,
                size: Self.estimatedSize,
                margin: Self.margin
            )
            HStack(spacing: 4) {
                action("Send to Input") { model.sendSelectionToInput() }
                divider
                action("Copy") { model.copySelection() }
                // Gated exactly like Android's `canPasteFromClipboard()`:
                // session accepting input, and the clipboard advertising text.
                // `Clipboard.hasText` is a description-level probe, so building
                // this toolbar never raises the iOS paste banner.
                if model.canSendTerminalBytes, Clipboard.hasText {
                    divider
                    action("Paste") { model.pasteFromClipboard() }
                }
                divider
                action("Cancel") { model.endSelection() }
            }
            .padding(.horizontal, 4)
            .padding(.vertical, 2)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(colors.surfaceVariant)
                    .shadow(radius: 6, y: 2)
            )
            .position(x: position.x, y: position.y)
            .accessibilityIdentifier("terminal.selectionToolbar")
        }
    }

    private var divider: some View {
        Rectangle()
            .fill(colors.onSurfaceVariant.opacity(0.25))
            .frame(width: 1, height: 20)
    }

    private func action(_ title: String, run: @escaping () -> Void) -> some View {
        Button(title, action: run)
            .font(.footnote)
            .buttonStyle(.borderless)
            .padding(.horizontal, 8)
            .padding(.vertical, 8)
    }

    /// Centres the toolbar just above the selection, flipping below it when
    /// there is no room and clamping to the container either way — the job
    /// Android's `ActionMode.onGetContentRect` hands to the platform.
    static func position(
        for anchor: CGRect,
        container: CGSize,
        size: CGSize,
        margin: CGFloat
    ) -> CGPoint {
        let halfWidth = size.width / 2
        let halfHeight = size.height / 2

        let above = anchor.minY - margin - halfHeight
        let below = anchor.maxY + margin + halfHeight
        var y = above
        if above - halfHeight < 0 {
            y = below
        }
        // Neither side fits (a selection taller than the viewport): keep it on
        // screen rather than letting it slide out of reach.
        y = min(max(halfHeight + margin, y), max(halfHeight + margin, container.height - halfHeight - margin))

        let x = min(
            max(halfWidth + margin, anchor.midX),
            max(halfWidth + margin, container.width - halfWidth - margin)
        )
        return CGPoint(x: x, y: y)
    }
}
