import SwiftUI

/// A left drawer over the host's content: a scrim plus a panel that slides in
/// from the leading edge. The host owns `isOpen`; `dragTranslation` is the
/// live finger offset of an edge pan the host runs (nil when idle), so the
/// panel follows the finger before the open decision is made. Dragging the
/// open panel leftwards closes it.
struct SideDrawer<Panel: View>: View {
    @Binding var isOpen: Bool
    let dragTranslation: CGFloat?
    var width: CGFloat = SideDrawer.defaultWidth
    @ViewBuilder let panel: () -> Panel

    static var defaultWidth: CGFloat { 300 }

    @Environment(\.mobaideColors) private var colors
    @State private var closeDrag: CGFloat = 0

    /// Horizontal offset of the panel: 0 fully open, `-width` fully hidden.
    private var offset: CGFloat {
        if let dragTranslation {
            return min(0, -width + max(0, dragTranslation))
        }
        return isOpen ? min(0, closeDrag) : -width
    }

    private var progress: CGFloat { (width + offset) / width }

    var body: some View {
        ZStack(alignment: .leading) {
            if progress > 0 {
                colors.scrim
                    .opacity(Double(progress))
                    .ignoresSafeArea()
                    .onTapGesture { isOpen = false }
                    .accessibilityHidden(true)
            }
            panel()
                .frame(width: width)
                .frame(maxHeight: .infinity)
                .background(colors.bg)
                .overlay(alignment: .trailing) {
                    Rectangle().fill(colors.surface2).frame(width: PixelMetrics.ring)
                }
                .offset(x: offset)
                .gesture(closeGesture)
                .accessibilityHidden(progress == 0)
        }
        // Fill the host and pin to its leading edge: a parent that centres
        // its children would otherwise place the hidden panel half on screen.
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .allowsHitTesting(isOpen || dragTranslation != nil)
        .animation(.easeOut(duration: 0.2), value: isOpen)
        .animation(.easeOut(duration: 0.2), value: dragTranslation == nil)
    }

    private var closeGesture: some Gesture {
        DragGesture(minimumDistance: 10)
            .onChanged { value in
                guard isOpen else { return }
                closeDrag = min(0, value.translation.width)
            }
            .onEnded { value in
                guard isOpen else { return }
                let shouldClose = value.translation.width < -width / 3
                    || value.predictedEndTranslation.width < -width / 2
                closeDrag = 0
                if shouldClose { isOpen = false }
            }
    }

    /// Whether an edge pan that ended at `translation` with `velocity` opens
    /// the drawer: a third of the way, or a flick.
    static func shouldOpen(translation: CGFloat, velocity: CGFloat, width: CGFloat = defaultWidth) -> Bool {
        translation > width / 3 || (translation > 20 && velocity > 300)
    }
}
