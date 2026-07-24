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
            guard displayConfig != oldValue else { return }
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

        addSubview(terminalView)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        let viewport = bounds.size
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

    /// Direction-aware arbitration so vertical scrollback still reaches the
    /// inner SwiftTerm view. `isDirectionalLockEnabled` only constrains this
    /// scroll view's own axis *after* its pan has begun; it does not stop a
    /// vertically dominant drag (with slight horizontal drift) from starting the
    /// horizontal pan and cancelling the terminal's vertical scroll. So in
    /// Static mode claim the pan only for *strictly* horizontally dominant drags
    /// and let vertical ones — including the exact-diagonal tie — fall through to
    /// SwiftTerm, mirroring Android
    /// `TerminalHorizontalScrollView.onInterceptTouchEvent` (intercept iff dx > dy).
    override func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        if displayConfig.mode == .static, gestureRecognizer === panGestureRecognizer {
            let translation = panGestureRecognizer.translation(in: self)
            if abs(translation.y) >= abs(translation.x) {
                return false
            }
        }
        return super.gestureRecognizerShouldBegin(gestureRecognizer)
    }
}
