import SwiftUI

extension View {
    /// Keeps the terminal of `sessionId` watched for as long as this screen is
    /// on the navigation stack (`ForegroundSessionTracker.holdWatch`), so a
    /// session-scoped child screen never reads as "nobody is watching".
    func holdsSessionWatch(_ sessionId: String) -> some View {
        modifier(SessionWatchHoldModifier(sessionId: sessionId))
    }
}

private struct SessionWatchHoldModifier: ViewModifier {
    let sessionId: String

    @Environment(\.appContainer) private var container
    /// SwiftUI can re-fire `onAppear` without a matching `onDisappear` (a
    /// cancelled interactive pop), and the tracker's count must move exactly
    /// once per visit.
    @State private var holding = false

    func body(content: Content) -> some View {
        content
            .onAppear {
                guard !holding else { return }
                holding = true
                container?.terminalSessionStore.holdWatch(sessionId: sessionId)
            }
            .onDisappear {
                guard holding else { return }
                holding = false
                container?.terminalSessionStore.releaseWatch(sessionId: sessionId)
            }
    }
}
