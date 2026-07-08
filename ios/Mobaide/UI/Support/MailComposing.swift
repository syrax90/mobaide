import MessageUI
import UIKit

/// Test seam over the platform's mail capabilities (Android `resolveActivity` /
/// `canSendMail` parity). `SupportModel` selects its mail-fallback chain purely
/// from these two probes, so the whole chain is unit-testable without
/// presenting UIKit.
@MainActor
protocol MailComposing: Sendable {
    /// Whether an in-app `MFMailComposeViewController` can send mail (false for
    /// Gmail-only users and all Mac Catalyst — the common no-Apple-Mail cases).
    var canSendMail: Bool { get }
    /// Whether the system can open `url` — used to probe a `mailto:` fallback.
    func canOpen(_ url: URL) -> Bool
}

/// Live implementation backed by MessageUI + UIApplication (both main-actor).
@MainActor
struct LiveMailComposer: MailComposing {
    var canSendMail: Bool { MFMailComposeViewController.canSendMail() }
    func canOpen(_ url: URL) -> Bool { UIApplication.shared.canOpenURL(url) }
}
