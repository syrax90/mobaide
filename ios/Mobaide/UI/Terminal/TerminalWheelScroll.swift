import CoreGraphics
import SwiftTerm

/// Routing rules for a vertical drag over the terminal, mirroring Android
/// `TerminalView.doScroll`.
///
/// A full-screen TUI runs on the alternate screen buffer, which SwiftTerm
/// creates with no scrollback at all — so its scroll view has nothing to move
/// and a drag is silently discarded. The fix is the same three-way ladder
/// Android has always used: report the drag to the program as wheel events when
/// it tracks the mouse, fall back to cursor keys on a bare alternate screen, and
/// only scroll SwiftTerm's own scrollback on the normal buffer.
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
