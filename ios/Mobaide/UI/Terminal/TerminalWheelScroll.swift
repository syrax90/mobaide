import CoreGraphics
import SwiftTerm

/// Routing rules for a touch over the terminal, mirroring Android
/// `TerminalView.doScroll` for drags and its `onUp`/`onSingleTapUp` split for
/// taps.
///
/// A full-screen TUI runs on the alternate screen buffer, which SwiftTerm
/// creates with no scrollback at all — so its scroll view has nothing to move
/// and a drag is silently discarded. The fix is the same three-way ladder
/// Android has always used: report the drag to the program as wheel events when
/// it tracks the mouse, fall back to cursor keys on a bare alternate screen, and
/// only scroll SwiftTerm's own scrollback on the normal buffer. A tap ladders on
/// the same three states — click, nothing, row-tap submit.
enum TerminalWheelScroll {
    /// Where a vertical drag's rows are delivered.
    enum Mode: Equatable {
        /// Mouse-reporting program: send xterm wheel buttons.
        case wheel
        /// Alternate screen without mouse reporting (`less`, `man`): send arrows.
        case cursorKeys
        /// Normal buffer: SwiftTerm's own scroll view owns the axis.
        case localScrollback
    }

    static let wheelUpFlags = 64
    static let wheelDownFlags = 65

    /// X10 mouse reports encode a coordinate as a single `32 + coord + 1` byte,
    /// and `Terminal.sendEvent` builds that byte *before* clamping it
    /// (`min(UInt8(255), UInt8(32 + x + 1))`), so a 0-based coordinate of 223 or
    /// more traps. Static display mode allows up to 240 columns and this is the
    /// first code in the app to reach `sendEvent`, so coordinates are clamped
    /// here rather than relying on a protocol that may not be SGR.
    static let maxEncodableCoordinate = 222

    /// Android `doScroll`'s priority order: mouse reporting wins over the
    /// alternate screen, which wins over local scrollback.
    static func mode(isMouseReporting: Bool, isAlternateBuffer: Bool) -> Mode {
        if isMouseReporting { return .wheel }
        if isAlternateBuffer { return .cursorKeys }
        return .localScrollback
    }

    /// Whether a tap should be reported to the program as a click.
    ///
    /// Deliberately stricter than `mode(isMouseReporting:)`, which routes every
    /// mode but `.off` to `.wheel`: SwiftTerm's `MouseMode.sendButtonPress()` is
    /// internal, so its rule is mirrored here, and it suppresses `.x10` presses.
    /// An `.x10` program therefore gets no click — and, because the tap still
    /// counts as mouse-reporting for the ladder, no row-tap submit either. An
    /// unrecognized future mode reports nothing rather than guessing.
    static func reportsClick(_ mode: Terminal.MouseMode) -> Bool {
        switch mode {
        case .vt200, .buttonEventTracking, .anyEvent: return true
        default: return false
        }
    }

    /// Vertical counterpart of `TerminalPanScrollView.shouldClaimHorizontalPan`,
    /// deliberately its exact complement on any non-zero sample: that one claims
    /// on `abs(x) > abs(y)`, so this one must claim every remaining direction,
    /// ties included. Velocity is the primary sample because a pan can be asked
    /// to begin while its translation is still zero.
    static func claimsPan(velocity: CGPoint, translation: CGPoint) -> Bool {
        let sample = velocity != .zero ? velocity : translation
        if sample == .zero { return false }
        return abs(sample.y) >= abs(sample.x)
    }

    static func wheelFlags(down: Bool) -> Int {
        down ? wheelDownFlags : wheelUpFlags
    }

    // MARK: - Fling

    /// Android `onFling`'s `SCALE`.
    static let flingVelocityScale: CGFloat = 0.25
    /// Lift-off speed below which nothing coasts, so a deliberate stop-then-lift
    /// ends the drag where the finger left it.
    ///
    /// Deliberately not Android's 50 dp/s
    /// `ViewConfiguration.getScaledMinimumFlingVelocity()`: that number is only
    /// a threshold because Android skips the cell-height division below, where
    /// it already buys a dozen rows. Divided by a real cell it buys none, so the
    /// threshold has to be restated in this formula's units — 250 pt/s is the
    /// first speed worth more than three rows.
    static let minimumFlingVelocity: CGFloat = 250
    static let flingMaxFrames = 12
    static let flingFrameInterval: Duration = .milliseconds(40)

    /// Rows a lift-off should keep scrolling, Android `onFling`'s
    /// `-(int)(velocityY * SCALE)` clamped to `±rows / 2`.
    ///
    /// Android feeds pixels-per-second straight into a row-space `Scroller`
    /// with no font-height division, so any real flick saturates that clamp.
    /// Dividing by the cell height instead makes the travel proportional to the
    /// flick, which matters more here: every row is a WebSocket round trip plus
    /// a full repaint from the program, where Android's events hit a local PTY.
    ///
    /// - Parameter velocityY: UIKit pan velocity, positive when the finger
    ///   moved down the screen.
    /// - Returns: rows toward newer content — positive means wheel-down — the
    ///   sign convention `TerminalWheelAccumulator.rowsDown` uses.
    static func flingRows(velocityY: CGFloat, cellHeight: CGFloat, rows: Int) -> Int {
        guard cellHeight > 0, velocityY.isFinite, abs(velocityY) >= minimumFlingVelocity else { return 0 }
        let travel = -velocityY * flingVelocityScale / cellHeight
        guard travel.isFinite else { return 0 }
        // Clamped before the conversion, not after: `Int(_: CGFloat)` traps on
        // anything past `Int.max`, which a cell height of a fraction of a point
        // reaches — the same class of trap as `maxEncodableCoordinate` above.
        let limit = CGFloat(max(1, rows / 2))
        return Int(min(limit, max(-limit, travel)))
    }

    /// Splits a fling's rows into per-frame counts that decelerate, the shape
    /// Android gets from `Scroller`'s spline.
    ///
    /// Quadratic ease-out on the *cumulative* travel — the distance a constant
    /// deceleration covers, which is what `Scroller` integrates. Differencing a
    /// rounded cumulative rather than rounding each step makes the steps sum to
    /// `rows` exactly, so `±rows / 2` is the real travel budget and not an
    /// approximation of one.
    ///
    /// At most two rows per frame, capped again at `flingMaxFrames`: a frame
    /// carrying less than a row is a 40ms stall rather than deceleration, and a
    /// long fling should coarsen into bigger steps instead of stretching into a
    /// longer stream of single-row round trips.
    static func flingSteps(rows: Int) -> [Int] {
        let total = abs(rows)
        guard total > 0 else { return [] }
        let frames = min(flingMaxFrames, (total + 1) / 2)
        let sign = rows > 0 ? 1 : -1
        var steps: [Int] = []
        steps.reserveCapacity(frames)
        var emitted = 0
        for frame in 1 ... frames {
            let remaining = 1 - Double(frame) / Double(frames)
            let travelled = Int((Double(total) * (1 - remaining * remaining)).rounded())
            defer { emitted = travelled }
            guard travelled != emitted else { continue }
            steps.append(sign * (travelled - emitted))
        }
        return steps
    }

    static func cursorKeyBytes(down: Bool, applicationCursor: Bool) -> [UInt8] {
        if down {
            return applicationCursor ? EscapeSequences.moveDownApp : EscapeSequences.moveDownNormal
        }
        return applicationCursor ? EscapeSequences.moveUpApp : EscapeSequences.moveUpNormal
    }

    /// Resolves a touch point in the terminal's content coordinates to a 0-based
    /// grid cell (`Terminal.sendEvent` applies the protocol's own +1).
    static func cell(
        at point: CGPoint,
        contentOffset: CGPoint,
        cellSize: CGSize,
        cols: Int,
        rows: Int
    ) -> (col: Int, row: Int) {
        (
            col: index(distance: point.x - contentOffset.x, cellExtent: cellSize.width, count: cols),
            row: index(distance: point.y - contentOffset.y, cellExtent: cellSize.height, count: rows)
        )
    }

    private static func index(distance: CGFloat, cellExtent: CGFloat, count: Int) -> Int {
        guard cellExtent > 0 else { return 0 }
        let upper = min(max(0, count - 1), maxEncodableCoordinate)
        let raw = (distance / cellExtent).rounded(.down)
        guard raw.isFinite, raw > 0 else { return 0 }
        return raw > CGFloat(upper) ? upper : Int(raw)
    }
}

/// Quantizes drag pixels into whole rows while carrying the sub-row leftover
/// across callbacks (Android `mScrollRemainder`).
/// Without the carry a slow drag never accumulates a full row and scrolls
/// nothing at all.
struct TerminalWheelAccumulator {
    private var remainder: CGFloat = 0

    mutating func reset() {
        remainder = 0
    }

    /// - Parameter panDeltaY: UIKit pan delta since the last call, positive when
    ///   the finger moved down the screen.
    /// - Returns: rows toward newer content — positive means wheel-down — which
    ///   is the sign convention Android's `doScroll(rowsDown:)` uses. Dragging
    ///   the finger down pulls older content into view, so the sign inverts.
    mutating func rowsDown(panDeltaY: CGFloat, cellHeight: CGFloat) -> Int {
        guard cellHeight > 0, panDeltaY.isFinite else { return 0 }
        let travel = -panDeltaY + remainder
        let rows = Int(travel / cellHeight)
        remainder = travel - CGFloat(rows) * cellHeight
        return rows
    }
}
