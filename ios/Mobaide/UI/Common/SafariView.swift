import SafariServices
import SwiftUI

/// SwiftUI wrapper over `SFSafariViewController` (Android Custom Tabs
/// equivalent). Present modally via `.sheet`; no coordinator is needed —
/// dismissal is driven by the presenting binding and the controller's own
/// Done button.
struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}
