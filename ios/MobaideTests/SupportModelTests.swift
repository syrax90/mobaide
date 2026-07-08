import XCTest
@testable import Mobaide

@MainActor
private struct FakeMailComposer: MailComposing {
    let canSendMail: Bool
    let canOpenResult: Bool
    func canOpen(_ url: URL) -> Bool { canOpenResult }
}

@MainActor
final class SupportModelTests: XCTestCase {
    private let deviceStore = DeviceIdentityStore(keychain: InMemoryKeychainStore())

    private func makeModel(
        canSendMail: Bool,
        canOpenMailto: Bool,
        userEmail: String? = "user@example.com"
    ) -> SupportModel {
        SupportModel(
            mailComposer: FakeMailComposer(canSendMail: canSendMail, canOpenResult: canOpenMailto),
            deviceIdentityStore: deviceStore,
            userEmail: userEmail,
            supportEmail: "support@mobaide.com",
            supportGitHubURL: "https://example.com/community",
            appVersion: "9.9.9"
        )
    }

    // MARK: - Fallback chain (canSendMail seam)

    func testCanSendMailResolvesToCompose() {
        let action = makeModel(canSendMail: true, canOpenMailto: true).resolveMailAction()
        guard case let .compose(recipient, subject, body) = action else { return XCTFail("expected compose") }
        XCTAssertEqual(recipient, "support@mobaide.com")
        XCTAssertEqual(subject, "Mobaide support — v9.9.9")
        XCTAssertTrue(body.contains("App version: 9.9.9"))
        XCTAssertTrue(body.contains("Account: user@example.com"))
    }

    func testNoComposerFallsBackToMailto() {
        let action = makeModel(canSendMail: false, canOpenMailto: true).resolveMailAction()
        guard case let .openURL(url) = action else { return XCTFail("expected mailto openURL") }
        XCTAssertEqual(url.scheme, "mailto")
        XCTAssertTrue(url.absoluteString.contains("support@mobaide.com"))
    }

    func testNoMailHandlerFallsBackToCopyAddress() {
        let action = makeModel(canSendMail: false, canOpenMailto: false).resolveMailAction()
        XCTAssertEqual(action, .copyAddress("support@mobaide.com"))
    }

    // MARK: - Prefill

    func testBodyOmitsAccountLineWhenSignedOut() {
        let action = makeModel(canSendMail: true, canOpenMailto: true, userEmail: nil).resolveMailAction()
        guard case let .compose(_, _, body) = action else { return XCTFail("expected compose") }
        XCTAssertFalse(body.contains("Account:"), "no Account line for a signed-out reporter")
        XCTAssertTrue(body.contains("Device ID:"))
    }

    func testOpenCommunitySetsSafariURL() {
        let model = makeModel(canSendMail: true, canOpenMailto: true)
        model.openCommunity()
        XCTAssertEqual(model.safariURL?.absoluteString, "https://example.com/community")
    }
}
