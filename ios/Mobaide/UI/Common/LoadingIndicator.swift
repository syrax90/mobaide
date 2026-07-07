import SwiftUI

/// Mirror of Android `LoadingIndicator`: a centered spinner filling the
/// available space.
struct LoadingIndicator: View {
    var body: some View {
        ProgressView()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
