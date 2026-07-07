import XCTest
@testable import Mobaide

@MainActor
final class GoogleSignInClientTests: XCTestCase {
    // Android GoogleAuthClientTest parity matrix.
    func testIsClientIdConfigured() {
        XCTAssertFalse(GoogleSignInClient.isClientIdConfigured(nil))
        XCTAssertFalse(GoogleSignInClient.isClientIdConfigured(""))
        XCTAssertFalse(GoogleSignInClient.isClientIdConfigured("   "))
        XCTAssertFalse(GoogleSignInClient.isClientIdConfigured("YOUR_WEB_CLIENT_ID"))
        XCTAssertFalse(GoogleSignInClient.isClientIdConfigured("YOUR_WEB_CLIENT_ID.apps.googleusercontent.com"))
        XCTAssertFalse(GoogleSignInClient.isClientIdConfigured("123-abc.example.com"))
        XCTAssertTrue(GoogleSignInClient.isClientIdConfigured("123-abc.apps.googleusercontent.com"))
    }

    func testIsReversedClientIdConfigured() {
        XCTAssertFalse(GoogleSignInClient.isReversedClientIdConfigured(nil))
        XCTAssertFalse(GoogleSignInClient.isReversedClientIdConfigured(""))
        XCTAssertFalse(GoogleSignInClient.isReversedClientIdConfigured("   "))
        XCTAssertFalse(GoogleSignInClient.isReversedClientIdConfigured("$(GOOGLE_REVERSED_CLIENT_ID)"))
        XCTAssertFalse(GoogleSignInClient.isReversedClientIdConfigured("123-abc.apps.googleusercontent.com"))
        XCTAssertTrue(GoogleSignInClient.isReversedClientIdConfigured("com.googleusercontent.apps.123-abc"))
    }

    func testSignInWithoutClientIdsReturnsNotConfigured() async {
        let client = GoogleSignInClient(clientID: nil, serverClientID: nil, reversedClientID: nil)
        let result = await client.signIn()
        XCTAssertEqual(result, .notConfigured)
    }

    func testSignInWithPlaceholderServerIdReturnsNotConfigured() async {
        let client = GoogleSignInClient(
            clientID: "123-abc.apps.googleusercontent.com",
            serverClientID: "$(GOOGLE_SERVER_CLIENT_ID)",
            reversedClientID: "com.googleusercontent.apps.123-abc"
        )
        let result = await client.signIn()
        XCTAssertEqual(result, .notConfigured)
    }

    func testSignInWithoutReversedSchemeReturnsNotConfigured() async {
        // Both client ids set but no redirect URL scheme: the flow could
        // start but never complete, so the guard must block it.
        let client = GoogleSignInClient(
            clientID: "123-abc.apps.googleusercontent.com",
            serverClientID: "456-web.apps.googleusercontent.com",
            reversedClientID: "$(GOOGLE_REVERSED_CLIENT_ID)"
        )
        let result = await client.signIn()
        XCTAssertEqual(result, .notConfigured)
    }

    func testResolveOptionalSetting() {
        XCTAssertNil(AppConfig.resolveOptionalSetting(nil))
        XCTAssertNil(AppConfig.resolveOptionalSetting(""))
        XCTAssertNil(AppConfig.resolveOptionalSetting("$(GOOGLE_IOS_CLIENT_ID)"))
        XCTAssertEqual(
            AppConfig.resolveOptionalSetting("123-abc.apps.googleusercontent.com"),
            "123-abc.apps.googleusercontent.com"
        )
    }
}
