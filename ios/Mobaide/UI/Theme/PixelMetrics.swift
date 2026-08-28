import SwiftUI

/// Geometry of the pixel kit (`--px` and friends in `theme.css`).
enum PixelMetrics {
    /// The atomic unit: hard-shadow offset and pressed-button travel.
    static let px: CGFloat = 4
    static let ring: CGFloat = 2
    static let statusSquare: CGFloat = 8
    static let checkbox: CGFloat = 18
    static let radio: CGFloat = 16
    static let dotTile: CGFloat = 24
    static let chromeTracking: CGFloat = 0.08

    static let buttonInsets = EdgeInsets(top: 9, leading: 16, bottom: 9, trailing: 16)
    static let smallButtonInsets = EdgeInsets(top: 5, leading: 11, bottom: 5, trailing: 11)
    static let fieldInsets = EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12)
    static let panelInsets = EdgeInsets(top: 22, leading: 24, bottom: 22, trailing: 24)
    static let cardInsets = EdgeInsets(top: 14, leading: 14, bottom: 14, trailing: 14)
    static let bannerInsets = EdgeInsets(top: 14, leading: 16, bottom: 14, trailing: 16)
    static let emptyStateInsets = EdgeInsets(top: 32, leading: 24, bottom: 32, trailing: 24)
    static let listRowInsets = EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16)
}
