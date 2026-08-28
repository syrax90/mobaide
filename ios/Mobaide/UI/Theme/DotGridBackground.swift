import SwiftUI
import UIKit

/// The page texture (`body { background-image: radial-gradient(...) }`): one
/// `surface2` dot per 24pt tile on `bg`. A tiled bitmap rather than a `Canvas`
/// so nothing is redrawn on scroll or resize.
struct DotGridBackground: View {
    @Environment(\.mobaideColors) private var colors
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.displayScale) private var displayScale

    var body: some View {
        ZStack {
            colors.bg
            Image(uiImage: DotGridTile.image(
                dot: UIColor(colors.surface2),
                key: colorScheme == .dark ? "dark" : "light",
                scale: displayScale
            ))
            .resizable(resizingMode: .tile)
        }
        .ignoresSafeArea()
    }
}

@MainActor
enum DotGridTile {
    private static var cache: [String: UIImage] = [:]

    static func image(dot: UIColor, key: String, scale: CGFloat) -> UIImage {
        let cacheKey = "\(key)@\(scale)"
        if let hit = cache[cacheKey] { return hit }
        let format = UIGraphicsImageRendererFormat()
        format.scale = scale
        format.opaque = false
        let side = PixelMetrics.dotTile
        let image = UIGraphicsImageRenderer(size: CGSize(width: side, height: side), format: format).image { _ in
            dot.setFill()
            UIRectFill(CGRect(x: side / 2 - 0.5, y: side / 2 - 0.5, width: 1, height: 1))
        }
        cache[cacheKey] = image
        return image
    }
}
