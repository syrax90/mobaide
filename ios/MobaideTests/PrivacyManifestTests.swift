import XCTest
@testable import Mobaide

/// App Store Connect rejects uploads (ITMS-91053) that call a Required Reason
/// API without declaring it in a bundled `PrivacyInfo.xcprivacy`. The app reads
/// `UserDefaults` in `PreferencesStore` and `TrustKeyPinning`, so the manifest
/// must ship in the bundle and keep that declaration. These assertions fail at
/// test time rather than at upload time if the file is dropped, renamed, or
/// stops being copied as a resource.
final class PrivacyManifestTests: XCTestCase {
    private var manifest: [String: Any]!

    override func setUpWithError() throws {
        try super.setUpWithError()
        let url = try XCTUnwrap(
            Bundle.main.url(forResource: "PrivacyInfo", withExtension: "xcprivacy"),
            "PrivacyInfo.xcprivacy is missing from the app bundle"
        )
        let data = try Data(contentsOf: url)
        let parsed = try PropertyListSerialization.propertyList(from: data, format: nil)
        manifest = try XCTUnwrap(parsed as? [String: Any], "manifest is not a dictionary")
    }

    func testDeclaresUserDefaultsWithAReason() throws {
        let types = try XCTUnwrap(manifest["NSPrivacyAccessedAPITypes"] as? [[String: Any]])
        let userDefaults = try XCTUnwrap(
            types.first { $0["NSPrivacyAccessedAPIType"] as? String == "NSPrivacyAccessedAPICategoryUserDefaults" },
            "UserDefaults is used by the app but not declared"
        )
        // CA92.1 = defaults readable only by this app. The stores use plain
        // custom suites, not an App Group, so the App Group reason (1C8F.1)
        // would be wrong — revisit only if an App Group entitlement is added.
        XCTAssertEqual(userDefaults["NSPrivacyAccessedAPITypeReasons"] as? [String], ["CA92.1"])
    }

    func testDeclaresNoTracking() {
        XCTAssertEqual(manifest["NSPrivacyTracking"] as? Bool, false)
        XCTAssertEqual((manifest["NSPrivacyTrackingDomains"] as? [String])?.isEmpty, true)
    }

    func testCollectedDataTypesAreLinkedNonTrackingAndPurposed() throws {
        let collected = try XCTUnwrap(manifest["NSPrivacyCollectedDataTypes"] as? [[String: Any]])
        XCTAssertEqual(
            Set(collected.compactMap { $0["NSPrivacyCollectedDataType"] as? String }),
            [
                "NSPrivacyCollectedDataTypeEmailAddress",
                "NSPrivacyCollectedDataTypeUserID",
                "NSPrivacyCollectedDataTypeDeviceID",
            ]
        )
        for entry in collected {
            let name = entry["NSPrivacyCollectedDataType"] as? String ?? "?"
            XCTAssertEqual(entry["NSPrivacyCollectedDataTypeTracking"] as? Bool, false, name)
            XCTAssertEqual(
                entry["NSPrivacyCollectedDataTypePurposes"] as? [String],
                ["NSPrivacyCollectedDataTypePurposeAppFunctionality"],
                name
            )
        }
    }
}
