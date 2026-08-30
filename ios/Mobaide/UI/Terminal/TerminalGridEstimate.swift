import UIKit

/// The grid a terminal will have, computed before one exists.
///
/// A session's PTY is spawned by the create request, minutes or hours before
/// its terminal is ever laid out — so the create screen has to name the grid up
/// front. Getting it wrong is not self-correcting: the program lays its opening
/// frames out at the spawn width, those bytes enter the agent's replay ring, and
/// every later reconnect re-renders them at the real grid.
///
/// Columns are the axis worth getting right. In `.static` they are exact and
/// need no geometry at all; rows are an estimate either way and self-correct on
/// the first real layout, which sends a resize.
enum TerminalGridEstimate {
    /// The face `MobaideTerminalView.configureAppearance` installs. Shared so a
    /// pre-layout estimate cannot drift from the view that later measures itself.
    static var font: UIFont { .monospacedSystemFont(ofSize: 13, weight: .regular) }

    /// SwiftTerm's own pixel-snapped `"W"` measurement
    /// (`AppleTerminalView.computeFontDimensions`).
    static func cellSize(font: UIFont = font, scale: CGFloat) -> CGSize {
        let measured = ("W" as NSString).size(withAttributes: [.font: font])
        let snap = { (value: CGFloat) in max(1, ceil(value * scale) / scale) }
        return CGSize(width: snap(measured.width), height: snap(measured.height))
    }

    /// Columns the terminal will derive for `config`.
    ///
    /// `.static` pins the count regardless of the device (the surface is drawn
    /// wider than the viewport and panned), so it is exact. `.full` divides the
    /// viewport the way SwiftTerm's `processSizeChange` does.
    static func columns(
        config: TerminalDisplayConfig,
        viewportWidth: CGFloat,
        cellWidth: CGFloat
    ) -> Int {
        switch config.mode {
        case .static:
            return config.staticColumns
        case .full:
            guard cellWidth > 0, viewportWidth > 0 else { return config.staticColumns }
            return max(1, Int(viewportWidth / cellWidth))
        }
    }

    /// Rows for a terminal occupying `viewportHeight`. An estimate: the caller
    /// cannot know the chrome the screen will subtract.
    static func rows(viewportHeight: CGFloat, cellHeight: CGFloat) -> Int {
        guard cellHeight > 0, viewportHeight > 0 else { return 24 }
        return max(1, Int(viewportHeight / cellHeight))
    }
}
