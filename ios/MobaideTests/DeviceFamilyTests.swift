import XCTest
@testable import Mobaide

/// The app ships iPhone-only. Declaring iPad support obliges 13" iPad
/// screenshots in App Store Connect and an app that genuinely works on iPad —
/// there is no iPad layout here, so a universal binary invites a Guideline 2.1
/// rejection. Widening `TARGETED_DEVICE_FAMILY` back to `"1,2"` is legitimate
/// only as the last step of building real iPad support and shooting its
/// screenshots, never as an edit to this test.
final class DeviceFamilyTests: XCTestCase {
    func testTargetsIPhoneOnly() throws {
        let families = try XCTUnwrap(
            Bundle.main.object(forInfoDictionaryKey: "UIDeviceFamily") as? [Int],
            "UIDeviceFamily is missing from the app bundle"
        )
        XCTAssertEqual(families, [1])
    }

    func testDeclaresNoIPadOrientations() {
        // Catches a re-added INFOPLIST_KEY_UISupportedInterfaceOrientations_iPad
        // even if the device family stayed correct.
        XCTAssertNil(Bundle.main.object(forInfoDictionaryKey: "UISupportedInterfaceOrientations~ipad"))
    }
}
