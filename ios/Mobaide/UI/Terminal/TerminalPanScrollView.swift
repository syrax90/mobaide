import UIKit

/// Hosts the retained `MobaideTerminalView` and realizes the two terminal
/// display modes (Android `TerminalHorizontalScrollView` + forced-columns
/// width). SwiftTerm derives its own column count from `bounds.width /
/// cellWidth`, so a fixed ("static") column count is produced by sizing the
/// terminal *wider than the viewport* and panning it horizontally here; "full"
/// lets it fill the viewport and reflow. The inner terminal keeps its own
/// vertical scrollback — this outer view never scrolls vertically because
/// `contentSize.height` is pinned to `bounds.height`, so the two axes hand off
/// cleanly (outer = horizontal pan, inner = vertical scrollback).
final class TerminalPanScrollView: UIScrollView {
    let terminalView: MobaideTerminalView

    var displayConfig: TerminalDisplayConfig {
        didSet {
            // Re-applied unconditionally (Android `AndroidView(update:)` parity):
            // layout is idempotent via its own frame/contentSize equality
            // checks, and an equality guard here would turn a dropped layout
            // pass (zero-bounds early-out, stale wrapper) into a permanently
            // missed mode change.
            alwaysBounceHorizontal = displayConfig.mode == .static
            setNeedsLayout()
        }
    }

    init(terminalView: MobaideTerminalView, displayConfig: TerminalDisplayConfig) {
        self.terminalView = terminalView
        self.displayConfig = displayConfig
        super.init(frame: .zero)

        isDirectionalLockEnabled = true
        showsHorizontalScrollIndicator = true
        showsVerticalScrollIndicator = false
        alwaysBounceVertical = false
        alwaysBounceHorizontal = displayConfig.mode == .static
        delaysContentTouches = false
        canCancelContentTouches = true
        isPagingEnabled = false
        scrollsToTop = false
        contentInsetAdjustmentBehavior = .never
        clipsToBounds = true

        terminalView.showsHorizontalScrollIndicator = false
        terminalView.contentInsetAdjustmentBehavior = .never
        // Nested UIScrollView pans recognize simultaneously; directional lock
        // on the inner keeps an outer-claimed horizontal pan from also
        // jiggling the scrollback vertically on near-diagonal drags.
        terminalView.isDirectionalLockEnabled = true

        addSubview(terminalView)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        let viewport = bounds.size
        // SwiftUI can keep an outgoing wrapper alive (and laying out) while
        // the next screen's wrapper has already re-parented the shared
        // terminal view; only the current owner may write its geometry.
        guard terminalView.superview === self else { return }
        guard viewport.width > 0, viewport.height > 0 else { return }

        let targetWidth: CGFloat
        switch displayConfig.mode {
        case .full:
            targetWidth = viewport.width
        case .static:
            let cellWidth = terminalView.cellSize.width
            targetWidth = cellWidth > 0
                ? ceil(CGFloat(displayConfig.staticColumns) * cellWidth)
                : viewport.width
        }

        let targetFrame = CGRect(x: 0, y: 0, width: targetWidth, height: viewport.height)
        if terminalView.frame != targetFrame {
            terminalView.frame = targetFrame
        }
        let targetContent = CGSize(width: targetWidth, height: viewport.height)
        if contentSize != targetContent {
            contentSize = targetContent
        }
    }

    /// Direction decision for the Static-mode horizontal pan, mirroring
    /// Android `TerminalHorizontalScrollView.onInterceptTouchEvent`
    /// (intercept iff dx > dy — ties yield to the terminal). Velocity is the
    /// primary sample: a UIScrollView pan can attempt to begin with a still
    /// zero translation, which a translation-only check misreads as a
    /// vertical tie and refuses — killing the pan for that whole touch.
    /// Translation is the fallback sample; when both are zero the touch is a
    /// stationary tap-to-stop, which must claim the pan only while a fling is
    /// being caught (`isDecelerating`).
    static func shouldClaimHorizontalPan(
        velocity: CGPoint,
        translation: CGPoint,
        isDecelerating: Bool
    ) -> Bool {
        let sample = velocity != .zero ? velocity : translation
        if sample == .zero { return isDecelerating }
        return abs(sample.x) > abs(sample.y)
    }

    /// Direction-aware arbitration so vertical scrollback still reaches the
    /// inner SwiftTerm view. `isDirectionalLockEnabled` only constrains this
    /// scroll view's own axis *after* its pan has begun; it does not stop a
    /// vertically dominant drag (with slight horizontal drift) from starting the
    /// horizontal pan and cancelling the terminal's vertical scroll. A refused
    /// pan stays refused for the rest of the touch sequence, so the per-touch
    /// decision is itself the Android-style latch.
    override func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        if displayConfig.mode == .static, gestureRecognizer === panGestureRecognizer {
            let claims = Self.shouldClaimHorizontalPan(
                velocity: panGestureRecognizer.velocity(in: self),
                translation: panGestureRecognizer.translation(in: self),
                isDecelerating: isDecelerating
            )
            if !claims {
                return false
            }
        }
        return super.gestureRecognizerShouldBegin(gestureRecognizer)
    }
}
