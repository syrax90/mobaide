import SwiftUI

/// Mirror of Android `LoadingIndicator`: the loading cue centred in the
/// available space — the pixel kit's blinking block, not a spinner.
struct LoadingIndicator: View {
    var body: some View {
        PixelLoadingBlock(width: 10, height: 18)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
