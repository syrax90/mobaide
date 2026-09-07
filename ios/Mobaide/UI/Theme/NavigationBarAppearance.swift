import UIKit

/// Web `.app-header`: an opaque `headBg` bar with a 2pt `surface2` bottom rule,
/// title in the chrome face. Installed once before the first `NavigationStack`
/// is built; trait-resolving colours make one appearance serve both themes.
@MainActor
enum PixelNavigationBarAppearance {
    static func install() {
        #if DEBUG
        if !MobaideTypography.chromeAvailable {
            assertionFailure("Departure Mono is not registered — check UIAppFonts in Info.plist")
        }
        #endif

        let bar = UINavigationBarAppearance()
        bar.configureWithOpaqueBackground()
        bar.backgroundColor = MobaideColorScheme.dynamic(\.headBg)
        bar.shadowColor = MobaideColorScheme.dynamic(\.surface2)
        bar.shadowImage = solidImage(height: PixelMetrics.ring)
        bar.titleTextAttributes = [
            .font: chromeFont(size: 17),
            .foregroundColor: MobaideColorScheme.dynamic(\.accent),
        ]
        bar.largeTitleTextAttributes = [
            .font: chromeFont(size: 22),
            .foregroundColor: MobaideColorScheme.dynamic(\.accent),
        ]
        let item = UIBarButtonItemAppearance(style: .plain)
        let itemAttributes: [NSAttributedString.Key: Any] = [
            .font: chromeFont(size: 13),
            .foregroundColor: MobaideColorScheme.dynamic(\.accent),
            .kern: 13 * PixelMetrics.chromeTracking,
        ]
        item.normal.titleTextAttributes = itemAttributes
        item.highlighted.titleTextAttributes = itemAttributes
        bar.buttonAppearance = item
        bar.doneButtonAppearance = item
        bar.backButtonAppearance = item

        let navigationBar = UINavigationBar.appearance()
        navigationBar.standardAppearance = bar
        navigationBar.scrollEdgeAppearance = bar
        navigationBar.compactAppearance = bar
        navigationBar.compactScrollEdgeAppearance = bar
        navigationBar.tintColor = MobaideColorScheme.dynamic(\.accent)

        let toolbar = UIToolbarAppearance()
        toolbar.configureWithOpaqueBackground()
        toolbar.backgroundColor = MobaideColorScheme.dynamic(\.surface)
        toolbar.shadowColor = MobaideColorScheme.dynamic(\.surface2)
        toolbar.shadowImage = solidImage(height: PixelMetrics.ring)
        toolbar.buttonAppearance = item
        toolbar.doneButtonAppearance = item
        UIToolbar.appearance().standardAppearance = toolbar
        UIToolbar.appearance().scrollEdgeAppearance = toolbar
        UIToolbar.appearance().compactAppearance = toolbar
    }

    private static func chromeFont(size: CGFloat) -> UIFont {
        UIFont(name: MobaideTypography.chromeFamily, size: size)
            ?? .monospacedSystemFont(ofSize: size, weight: .regular)
    }

    /// A template image `shadowColor` tints; the height is the rule's weight.
    private static func solidImage(height: CGFloat) -> UIImage {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 0
        let size = CGSize(width: 1, height: height)
        return UIGraphicsImageRenderer(size: size, format: format)
            .image { context in
                UIColor.white.setFill()
                context.fill(CGRect(origin: .zero, size: size))
            }
            .withRenderingMode(.alwaysTemplate)
    }
}
