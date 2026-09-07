import SwiftUI

/// Web `.state-loading`: the only progress indicator is a block cursor
/// blinking at 900 ms with a stepped, never eased, 50% duty cycle. Under
/// Reduce Motion the block stays lit as the "working" cue.
struct PixelLoadingBlock: View {
    var width: CGFloat = 8
    var height: CGFloat = 14

    @Environment(\.mobaideColors) private var colors
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.45)) { context in
            let phase = Int((context.date.timeIntervalSinceReferenceDate / 0.45).rounded(.down))
            Rectangle()
                .fill(colors.accent)
                .frame(width: width, height: height)
                .opacity(reduceMotion || phase % 2 == 0 ? 1 : 0)
        }
        .accessibilityLabel("Loading")
        .accessibilityAddTraits(.updatesFrequently)
    }
}

/// A modal "busy" plate over the scrim.
struct PixelBusyOverlay: View {
    var text: String? = nil

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        ZStack {
            colors.scrim.ignoresSafeArea()
            HStack(spacing: 12) {
                PixelLoadingBlock()
                if let text {
                    Text(text)
                        .chromeText()
                        .foregroundStyle(colors.text)
                }
            }
            .padding(EdgeInsets(top: 16, leading: 20, bottom: 16, trailing: 20))
            .background(colors.bg2)
            .pixelRing(colors.surface2)
            .pixelShadow(MobaideColorScheme.modalShadow)
        }
    }
}
