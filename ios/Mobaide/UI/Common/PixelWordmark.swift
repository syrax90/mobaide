import SwiftUI

/// The pixel wordmark (`web/public/wordmark.png`, 227×40, shipped at 1x only).
/// It is dark ink drawn for a light background, so it always sits on the
/// `plate` chip and scales nearest-neighbour — never smoothed.
struct PixelWordmark: View {
    var width: CGFloat = 128

    var body: some View {
        Image("Wordmark")
            .resizable()
            .interpolation(.none)
            .antialiased(false)
            .aspectRatio(227.0 / 40.0, contentMode: .fit)
            .frame(width: width)
            .padding(EdgeInsets(top: 5, leading: 8, bottom: 5, trailing: 8))
            .background(MobaideColorScheme.plate)
            .accessibilityLabel("Mobaide")
    }
}
