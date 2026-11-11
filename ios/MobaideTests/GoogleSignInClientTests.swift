import XCTest
@testable import Mobaide

@MainActor
final class GoogleSignInClientTests: XCTestCase {
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
}
