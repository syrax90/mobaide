import Foundation
import Observation

/// The resolved mail-fallback action (Android chain: MFMailCompose →
/// `mailto:` → copy the address).
enum SupportMailAction: Equatable, Sendable {
    case compose(recipient: String, subject: String, body: String)
    case openURL(URL)
    case copyAddress(String)
}

/// Backing model for the Support screen (Android `SupportViewModel` +
/// `EmailSupport`). Builds the prefilled diagnostics and resolves the mail
/// fallback purely from the `MailComposing` seam.
@MainActor
@Observable
final class SupportModel {
    var mailAction: SupportMailAction?
    var safariURL: URL?
    var message: String?

    let supportEmail: String

    private let mailComposer: any MailComposing
    private let supportGitHubURL: String
    private let subject: String
    private let body: String

    init(
        mailComposer: any MailComposing,
        deviceIdentityStore: DeviceIdentityStore,
        userEmail: String?,
        supportEmail: String = AppConfig.supportEmail,
        supportGitHubURL: String = AppConfig.supportGitHubURL,
        appVersion: String = AppConfig.appVersion
    ) {
        self.mailComposer = mailComposer
        self.supportEmail = supportEmail
        self.supportGitHubURL = supportGitHubURL
        self.subject = Self.buildSubject(version: appVersion)
        self.body = Self.buildBody(
            version: appVersion,
            deviceName: deviceIdentityStore.getDeviceName(),
            deviceId: deviceIdentityStore.getOrCreateDeviceId(),
            userEmail: userEmail
        )
    }

    func openCommunity() {
        safariURL = URL(string: supportGitHubURL)
    }

    func emailSupport() {
        mailAction = resolveMailAction()
    }

    /// Pure mail-fallback decision (Android `sendSupportEmail` chain), exposed
    /// for tests: in-app composer → `mailto:` handler → copy the address.
    func resolveMailAction() -> SupportMailAction {
        if mailComposer.canSendMail {
            return .compose(recipient: supportEmail, subject: subject, body: body)
        }
        if let mailto = Self.mailtoURL(recipient: supportEmail, subject: subject, body: body),
           mailComposer.canOpen(mailto) {
            return .openURL(mailto)
        }
        return .copyAddress(supportEmail)
    }

    func copyAddress(_ address: String) {
        Clipboard.copy(address)
        message = "No email app found — support address copied to clipboard."
    }

    func clearMailAction() { mailAction = nil }
    func clearMessage() { message = nil }

    // MARK: - Prefill (Android EmailSupport parity)

    static func buildSubject(version: String) -> String {
        "Mobaide support — v\(version)"
    }

    static func buildBody(version: String, deviceName: String, deviceId: String, userEmail: String?) -> String {
        var lines = [
            "Describe your question or issue here:",
            "",
            "",
            "———",
            "Diagnostics (please keep — helps us troubleshoot):",
            "App version: \(version)",
            "Device: \(deviceName)",
            "Device ID: \(deviceId)",
        ]
        if let userEmail, !userEmail.isEmpty {
            lines.append("Account: \(userEmail)")
        }
        return lines.joined(separator: "\n")
    }

    static func mailtoURL(recipient: String, subject: String, body: String) -> URL? {
        var components = URLComponents()
        components.scheme = "mailto"
        components.path = recipient
        components.queryItems = [
            URLQueryItem(name: "subject", value: subject),
            URLQueryItem(name: "body", value: body),
        ]
        return components.url
    }
}
