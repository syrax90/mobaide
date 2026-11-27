import XCTest
@testable import Mobaide

final class RelayRoutingTests: XCTestCase {
    func testKnownChannelRewrites() {
        XCTAssertEqual(RelayRouting.mapToRelayPath("ws/events", agentId: "A1"), "relay/ws/events/A1")
        XCTAssertEqual(RelayRouting.mapToRelayPath("ws/sessions", agentId: "A1"), "relay/ws/sessions/A1")
        XCTAssertEqual(RelayRouting.mapToRelayPath("ws/autoscan", agentId: "A1"), "relay/ws/autoscan/A1")
    }

    func testFallbackRewriteWrapsUnknownPaths() {
        XCTAssertEqual(
            RelayRouting.mapToRelayPath("ws/other/x", agentId: "A1"),
            "relay/ws/other/x/A1"
        )
    }

    func testRelayHostedChannelUsesWsForHTTPBase() {
        let url = RelayRouting.webSocketURLString(
            relayServerURL: "http://192.168.1.5:8000",
            path: "ws/user-events",
            token: "t"
        )
        XCTAssertEqual(url, "ws://192.168.1.5:8000/ws/user-events?token=t")
    }

    func testRelayHostedChannelUsesWssForHTTPSBaseAndKeepsThePath() {
        let url = RelayRouting.webSocketURLString(
            relayServerURL: "https://relay.example.com",
            path: "ws/user-events",
            token: "tok"
        )
        XCTAssertEqual(url, "wss://relay.example.com/ws/user-events?token=tok")
    }

    func testAgentChannelRewritesPathAndKeepsRawToken() {
        let url = RelayRouting.agentWebSocketURLString(
            relayServerURL: "https://relay.example.com",
            path: "ws/session/s1",
            agentId: "A1",
            token: "ab+c/d="
        )
        XCTAssertEqual(url, "wss://relay.example.com/relay/ws/session/A1/s1?token=ab+c/d=")
    }

    func testTokenAppendsWithAmpersandWhenQueryPresent() {
        let url = RelayRouting.webSocketURLString(
            relayServerURL: "https://host",
            path: "ws/events?foo=1",
            token: "t"
        )
        XCTAssertEqual(url, "wss://host/ws/events?foo=1&token=t")
    }

    func testAppendQueryItemChainsOntoAnExistingQuery() {
        XCTAssertEqual(
            RelayRouting.appendQueryItem(to: "wss://host/ws/user-events?token=t", name: "device_id", value: "D-1"),
            "wss://host/ws/user-events?token=t&device_id=D-1",
            "the user channel appends its device hint after the token"
        )
    }

    func testTrailingSlashBaseDoesNotDoubleSlashWebSocketURL() {
        let url = RelayRouting.agentWebSocketURLString(
            relayServerURL: "https://relay.example.com/",
            path: "ws/events",
            agentId: "A1",
            token: "t"
        )
        XCTAssertEqual(url, "wss://relay.example.com/relay/ws/events/A1?token=t")
    }

    func testTrailingSlashRelayURLDoesNotDoubleSlashRESTBase() {
        XCTAssertEqual(
            RelayRouting.agentRESTBaseURL(
                relayServerURL: "https://relay.example.com//",
                agentId: "A1"
            ),
            "https://relay.example.com/proxy/A1"
        )
    }

    func testAgentRESTBaseURLRequiresTheRelayAndTheAgentId() {
        XCTAssertNil(
            RelayRouting.agentRESTBaseURL(relayServerURL: "https://relay.example.com", agentId: nil)
        )
        XCTAssertNil(RelayRouting.agentRESTBaseURL(relayServerURL: nil, agentId: "A1"))
    }
}
