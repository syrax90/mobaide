import SwiftUI

/// A session row's leading mark: the walking pixel square while the agent
/// works, otherwise the static status square. Decorative — the chip next to the row carries the words.
struct SessionStatusIndicator: View {
    let session: SessionResponse

    var body: some View {
        if SessionDisplay.isWorking(session) {
            PixelWorkingSquare()
        } else {
            PixelStatusSquare(tone: SessionDisplay.stateTone(session.state))
        }
    }
}
