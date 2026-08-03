import SwiftUI
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

    /// Keyboard overlap above the home-indicator line while the keyboard is
    /// up (Android `ime.bottom - systemBars.bottom`). Drives the caret
    /// counter-pan in `layoutSubviews`; never touches any size, so keyboard
    /// transitions cannot fire `sizeChanged`.
    private(set) var keyboardLift: CGFloat = 0

    /// Test seam: overrides the caret-derived anchor (production nil).
    var caretAnchorBottomOverride: CGFloat?

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
        // The strip revealed above a counter-panned terminal must read as
        // terminal background, not as a hole in the layout.
        backgroundColor = UIColor(MobaideColors.terminalBackground)

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

        // Keyboard counter-pan moves only the frame ORIGIN — SwiftTerm resizes
        // solely from a bounds-size change, so this can never emit an `r` frame.
        let dy = currentCounterPan(boxHeight: viewport.height)
        let targetFrame = CGRect(x: 0, y: dy, width: targetWidth, height: viewport.height)
        if terminalView.frame != targetFrame {
            terminalView.frame = targetFrame
        }
        let targetContent = CGSize(width: targetWidth, height: viewport.height)
        if contentSize != targetContent {
            contentSize = targetContent
        }
    }

    /// Applies a new keyboard overlap, animating the resulting counter-pan
    /// with the keyboard's own spring so it cancels the screen's column lift
    /// exactly when the net movement should be zero.
    func setKeyboardLift(_ lift: CGFloat, duration: TimeInterval) {
        guard lift != keyboardLift else { return }
        keyboardLift = lift
        setNeedsLayout()
        guard window != nil, duration > 0 else { return }
        let timing = UISpringTimingParameters(
            mass: TerminalKeyboardPan.springMass,
            stiffness: TerminalKeyboardPan.springStiffness,
            damping: TerminalKeyboardPan.springDamping,
            initialVelocity: .zero
        )
        let animator = UIViewPropertyAnimator(duration: duration, timingParameters: timing)
        animator.addAnimations { self.layoutIfNeeded() }
        animator.startAnimation()
    }

    /// Re-evaluates the counter-pan when the caret or scroll offset moves
    /// (a TUI's input box can travel while the keyboard is up). Zero-cost
    /// while the keyboard is hidden and the terminal sits at its rest origin;
    /// otherwise UIKit coalesces the layout passes per display cycle.
    func caretDidMove() {
        guard keyboardLift > 0 || terminalView.frame.origin.y != 0 else { return }
        setNeedsLayout()
    }

    private func currentCounterPan(boxHeight: CGFloat) -> CGFloat {
        let anchor = caretAnchorBottomOverride ?? TerminalKeyboardPan.caretAnchorBottom(
            caretFrame: terminalView.caretFrame,
            contentOffsetY: terminalView.contentOffset.y,
            cellHeight: terminalView.cellSize.height
        )
        return TerminalKeyboardPan.counterPan(boxHeight: boxHeight, caretAnchorBottom: anchor, lift: keyboardLift)
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

    /// What this view *would* claim for a drag someone else sampled — the outer
    /// half of the terminal's axis split, so `MobaideTerminalView` can refuse its
    /// scrollback pan on exactly those drags. Deliberately not named
    /// `claimsHorizontalPan`: it answers a hypothetical about a supplied sample
    /// and drops the live-fling term below, so the two are not interchangeable.
    ///
    /// The child passes its *own* recognizer's numbers rather than reading this
    /// view's: UIKit asks a recognizer to begin only once that recognizer has
    /// seen enough travel, so its own sample is the only one guaranteed live at
    /// its decision moment — the outer's pan can still be sitting in `.possible`
    /// reporting zero for both, the same hazard `shouldClaimHorizontalPan`
    /// documents above.
    ///
    /// A zero sample therefore yields the touch to the scrollback instead of
    /// refusing it, matching `TerminalWheelScroll.claimsPan`: `isDecelerating`
    /// answers "should the outer catch its own fling?", which is not a question
    /// about which axis owns a drag, and inheriting it would let a decelerating
    /// horizontal fling swallow a vertical drag for its whole touch.
    ///
    /// Full mode has no horizontal overflow and never arbitrates, so the outer
    /// claims nothing there.
    func wouldClaimHorizontalPan(velocity: CGPoint, translation: CGPoint) -> Bool {
        guard displayConfig.mode == .static else { return false }
        return Self.shouldClaimHorizontalPan(
            velocity: velocity,
            translation: translation,
            isDecelerating: false
        )
    }

    /// The same split answered for this view's own pan, where a stationary touch
    /// must still catch an in-flight fling.
    func claimsHorizontalPan() -> Bool {
        guard displayConfig.mode == .static else { return false }
        return Self.shouldClaimHorizontalPan(
            velocity: panGestureRecognizer.velocity(in: self),
            translation: panGestureRecognizer.translation(in: self),
            isDecelerating: isDecelerating
        )
    }

    /// Direction-aware arbitration so vertical scrollback still reaches the
    /// inner SwiftTerm view. `isDirectionalLockEnabled` only constrains this
    /// scroll view's own axis *after* its pan has begun; it does not stop a
    /// vertically dominant drag (with slight horizontal drift) from starting the
    /// horizontal pan and cancelling the terminal's vertical scroll. A refused
    /// pan stays refused for the rest of the touch sequence, so the per-touch
    /// decision is itself the Android-style latch.
    ///
    /// The `.static` check is not redundant with `claimsHorizontalPan()`'s own
    /// mode guard: that guard answers on behalf of the *inner* view, for which
    /// Full mode means "nothing to yield to". Refusing on `!claimsHorizontalPan()`
    /// alone would also refuse this view's own pan in Full mode, where it is
    /// allowed today.
    override func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        // This pan lives on the outer view, so the inner view's sticky-selection
        // gate never sees it — a Static-mode horizontal drag would otherwise pan
        // the terminal out from under a selection the user is extending.
        if gestureRecognizer === panGestureRecognizer, terminalView.isSelectionActive {
            return false
        }
        if displayConfig.mode == .static,
           gestureRecognizer === panGestureRecognizer,
           !claimsHorizontalPan() {
            return false
        }
        return super.gestureRecognizerShouldBegin(gestureRecognizer)
    }
}
