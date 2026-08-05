import XCTest
@testable import Mobaide

final class RelayRoutingTests: XCTestCase {
    func testSessionPathRewrite() {
        XCTAssertEqual(
            RelayRouting.mapToRelayPath("ws/session/s1", agentId: "A1"),
            "relay/ws/session/A1/s1"
        )
    }

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

    func testDirectURLUsesWsForHTTPBase() {
        let url = RelayRouting.webSocketURLString(
            baseURL: "http://192.168.1.5:8000",
            path: "ws/events",
            mode: .direct,
            token: "t"
        )
        XCTAssertEqual(url, "ws://192.168.1.5:8000/ws/events?token=t")
    }

    func testDirectURLUsesWssForHTTPSBase() {
        let url = RelayRouting.webSocketURLString(
            baseURL: "https://agent.example.com",
            path: "ws/session/s1",
            mode: .direct,
            token: "tok"
        )
        XCTAssertEqual(url, "wss://agent.example.com/ws/session/s1?token=tok")
    }

    func testRelayURLRewritesPathAndKeepsRawToken() {
        let url = RelayRouting.webSocketURLString(
            baseURL: "https://relay.example.com",
            path: "ws/session/s1",
            mode: .relay,
            agentId: "A1",
            token: "ab+c/d="
        )
        XCTAssertEqual(url, "wss://relay.example.com/relay/ws/session/A1/s1?token=ab+c/d=")
    }

    func testTokenAppendsWithAmpersandWhenQueryPresent() {
        let url = RelayRouting.webSocketURLString(
            baseURL: "https://host",
            path: "ws/events?foo=1",
            mode: .direct,
            token: "t"
        )
        XCTAssertEqual(url, "wss://host/ws/events?foo=1&token=t")
    }

    func testAppendQueryItemOpensTheQueryWhenNoneIsPresent() {
        XCTAssertEqual(
            RelayRouting.appendQueryItem(to: "wss://host/ws/user-events", name: "device_id", value: "D-1"),
            "wss://host/ws/user-events?device_id=D-1"
        )
    }

    func testAppendQueryItemChainsOntoAnExistingQuery() {
        XCTAssertEqual(
            RelayRouting.appendQueryItem(to: "wss://host/ws/user-events?token=t", name: "device_id", value: "D-1"),
            "wss://host/ws/user-events?token=t&device_id=D-1",
            "the user channel appends its device hint after the token"
        )
    }

    func testAgentRESTBaseURLDirect() {
        XCTAssertEqual(
            RelayRouting.agentRESTBaseURL(
                mode: .direct,
                agentURL: "https://agent.example.com",
                relayServerURL: "https://relay.example.com",
                agentId: "A1"
            ),
            "https://agent.example.com"
        )
    }

    func testAgentRESTBaseURLRelayUsesProxyPath() {
        XCTAssertEqual(
            RelayRouting.agentRESTBaseURL(
                mode: .relay,
                agentURL: nil,
                relayServerURL: "https://relay.example.com",
                agentId: "A1"
            ),
            "https://relay.example.com/proxy/A1"
        )
    }

    func testTrailingSlashBaseDoesNotDoubleSlashWebSocketURL() {
        let url = RelayRouting.webSocketURLString(
            baseURL: "https://relay.example.com/",
            path: "ws/events",
            mode: .relay,
            agentId: "A1",
            token: "t"
        )
        XCTAssertEqual(url, "wss://relay.example.com/relay/ws/events/A1?token=t")
    }

    func testTrailingSlashRelayURLDoesNotDoubleSlashRESTBase() {
        XCTAssertEqual(
            RelayRouting.agentRESTBaseURL(
                mode: .relay,
                agentURL: nil,
                relayServerURL: "https://relay.example.com//",
                agentId: "A1"
            ),
            "https://relay.example.com/proxy/A1"
        )
    }

    func testAgentRESTBaseURLRelayRequiresAgentId() {
        XCTAssertNil(
            RelayRouting.agentRESTBaseURL(
                mode: .relay,
                agentURL: nil,
                relayServerURL: "https://relay.example.com",
                agentId: nil
            )
        )
    }
}
