import UIKit
import XCTest
@testable import Mobaide

/// The test bundle is hosted by the app, so `UIAppFonts` is in effect here:
/// a wrong PostScript name or a missing plist entry fails loudly instead of
/// silently falling back to the platform monospace.
final class ChromeFontTests: XCTestCase {
    func testDepartureMonoIsRegistered() {
        XCTAssertNotNil(UIFont(name: MobaideTypography.chromeFamily, size: 11))
        XCTAssertTrue(UIFont.familyNames.contains(MobaideTypography.chromeFamilyName))
        XCTAssertTrue(MobaideTypography.chromeAvailable)
    }
}
