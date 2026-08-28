import SwiftUI

/// Full-screen upgrade takeover (Android `IncompatibleServerScreen`), rendered
/// outside the `NavigationStack` while `AuthState` is `.incompatibleServer`.
/// Shows a store link (App Store deep link, falling back to the web listing when
/// the deep link is unset) and a "What changed" link.
///
/// `info.reason` is server-controlled on the 4002 close path, where in direct
/// mode the peer is the paired agent host. Nothing reads it; it stays only for
/// state parity with Android and web. Do not render it.
struct IncompatibleServerScreen: View {
    let info: AuthState.IncompatibleServer

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                PixelWordmark(width: 152)
                VStack(spacing: 16) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 44))
                        .foregroundStyle(colors.warning)
                    Text("Update required")
                        .pageTitle()
                    // Set membership, not a floor: the gate also fires when the app is
                    // newer than the relay, so the copy must not name a direction.
                    Text("This app version can no longer connect to the server. Check for an update to continue.")
                        .font(MobaideTypography.bodyLarge)
                        .foregroundStyle(colors.text)
                        .multilineTextAlignment(.center)
                    if let storeURL {
                        Link("Update from the App Store", destination: storeURL)
                            .buttonStyle(.pixelPrimary)
                    }
                    if let changesURL = URL(string: AppConfig.whatsChangedURL) {
                        Link("What changed", destination: changesURL)
                            .font(.footnote)
                    }
                }
                .frame(maxWidth: .infinity)
                .pixelPanel()
            }
            .padding(24)
        }
        .pixelScreenBackground()
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
