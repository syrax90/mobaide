import XCTest
@testable import Mobaide

final class PushEnvironmentTests: XCTestCase {
    func testDevelopmentMapsToSandbox() {
        let profile = """
        <key>application-identifier</key>
        <string>TEAM123456.com.mobaide.app</string>
        <key>aps-environment</key>
        <string>development</string>
        """
        XCTAssertEqual(PushEnvironment.parse(provisionText: profile), "sandbox")
    }

    func testProductionMapsToProduction() {
        let profile = "<key>aps-environment</key><string>production</string>"
        XCTAssertEqual(PushEnvironment.parse(provisionText: profile), "production")
    }

    func testDetectWithoutProfileFallsBack() {
        // The test bundle has no embedded.mobileprovision: simulator builds
        // report sandbox, a device/CI bundle without a profile reports
        // production — either way detect() must not crash and must return a
        // valid environment string.
        let detected = PushEnvironment.detect(bundle: Bundle(for: Self.self))
        XCTAssertTrue(["sandbox", "production"].contains(detected))
    }
}
