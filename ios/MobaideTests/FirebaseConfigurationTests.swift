import XCTest
@testable import Mobaide

/// The Firebase Analytics posture lives in Info.plist: Google's SKAdNetwork
/// id for iOS App campaigns, consent-mode defaults, and the automatic
/// collection the app opts out of. Pinned so a plist edit cannot quietly
/// widen what the SDK collects or prompt for tracking.
final class FirebaseConfigurationTests: XCTestCase {
    private var info: [String: Any]!

    override func setUpWithError() throws {
        try super.setUpWithError()
        info = try XCTUnwrap(Bundle.main.infoDictionary)
    }

    func testGoogleSKAdNetworkIdentifierIsListed() throws {
        let items = try XCTUnwrap(info["SKAdNetworkItems"] as? [[String: Any]])
        XCTAssertEqual(
            items.compactMap { $0["SKAdNetworkIdentifier"] as? String },
            ["cstr6suwn9.skadnetwork"]
        )
    }

    func testAutomaticCollectionAndSwizzlingAreOff() {
        XCTAssertEqual(info["FirebaseAutomaticScreenReportingEnabled"] as? Bool, false)
        XCTAssertEqual(info["FirebaseAppDelegateProxyEnabled"] as? Bool, false)
        XCTAssertEqual(info["GOOGLE_ANALYTICS_IDFV_COLLECTION_ENABLED"] as? Bool, false)
    }

    func testConsentDefaultsDenyPersonalisationAndFollowTheEUPolicy() {
        XCTAssertEqual(info["GOOGLE_ANALYTICS_DEFAULT_ALLOW_ANALYTICS_STORAGE"] as? Bool, true)
        XCTAssertEqual(info["GOOGLE_ANALYTICS_DEFAULT_ALLOW_AD_STORAGE"] as? Bool, true)
        XCTAssertEqual(info["GOOGLE_ANALYTICS_DEFAULT_ALLOW_AD_USER_DATA"] as? String, "eu_consent_policy")
        XCTAssertEqual(info["GOOGLE_ANALYTICS_DEFAULT_ALLOW_AD_PERSONALIZATION_SIGNALS"] as? Bool, false)
    }

    func testNoTrackingPromptIsDeclared() {
        XCTAssertNil(info["NSUserTrackingUsageDescription"])
    }
}
