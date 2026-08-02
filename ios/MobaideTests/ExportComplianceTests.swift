import XCTest
@testable import Mobaide

/// Without `ITSAppUsesNonExemptEncryption` in the bundle, every upload parks in
/// App Store Connect behind the manual export-compliance questionnaire, which
/// breaks unattended release automation. `false` is the correct classification
/// only while the app's sole cryptography stays exempt: SHA-256 hashing (a hash,
/// not encryption) in `SignInContract`, OS-provided TLS via `URLSession`, and
/// the Keychain. Adding proprietary or non-exempt encryption — encrypting
/// terminal traffic beyond TLS, bundling a crypto library — invalidates it and
/// requires a real self-classification, not an edit to this test.
final class ExportComplianceTests: XCTestCase {
    func testDeclaresOnlyExemptEncryption() throws {
        let declared = try XCTUnwrap(
            Bundle.main.object(forInfoDictionaryKey: "ITSAppUsesNonExemptEncryption") as? Bool,
            "ITSAppUsesNonExemptEncryption is missing from the app bundle"
        )
        XCTAssertFalse(declared)
    }
}
