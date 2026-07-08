import SwiftUI

/// Full-screen upgrade takeover (Android `IncompatibleServerScreen`), rendered
/// outside the `NavigationStack` while `AuthState` is `.incompatibleServer`.
/// Shows the server's reason, a store link (App Store deep link, falling back
/// to the web listing when the deep link is unset), and a "What changed" link.
struct IncompatibleServerScreen: View {
    let info: AuthState.IncompatibleServer

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 48))
                .foregroundStyle(MobaideColors.warningYellow)
            Text("Update required")
                .font(MobaideTypography.titleLarge)
            Text(info.reason)
                .font(MobaideTypography.bodyLarge)
                .multilineTextAlignment(.center)
            if let storeURL {
                Link("Update from the App Store", destination: storeURL)
                    .buttonStyle(.borderedProminent)
            }
            if let changesURL = URL(string: AppConfig.whatsChangedURL) {
                Link("What changed", destination: changesURL)
                    .font(.footnote)
            }
        }
        .padding(24)
    }

    /// Prefer the deep link; fall back to the web listing when it is empty.
    private var storeURL: URL? {
        if !info.storeUrl.isEmpty, let url = URL(string: info.storeUrl) {
            return url
        }
        if !info.storeFallbackUrl.isEmpty, let url = URL(string: info.storeFallbackUrl) {
            return url
        }
        return nil
    }
}
