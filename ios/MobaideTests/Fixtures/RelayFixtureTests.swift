import XCTest
@testable import Mobaide

private struct RelayFixtureFile: Decodable {
    let path_cases: [PathCase]
    let url_cases: [URLCase]
    let rest_base_cases: [RESTBaseCase]

    struct PathCase: Decodable {
        let path: String
        let agent_id: String
        let expect: String
    }

    struct URLCase: Decodable {
        let name: String
        let base: String
        let mode: String
        let path: String
        let agent_id: String?
        let token: String
        let expect: String
    }

    struct RESTBaseCase: Decodable {
        let name: String
        let mode: String
        let agent_url: String?
        let relay_url: String?
        let agent_id: String?
        let expect: String?
    }
}

final class RelayFixtureTests: XCTestCase {
    private func loadFile() throws -> RelayFixtureFile {
        try FixtureLoader.load(RelayFixtureFile.self, path: "relay/relay_mapping.json")
    }

    func testPathMappingTable() throws {
        let file = try loadFile()
        XCTAssertFalse(file.path_cases.isEmpty)
        for pathCase in file.path_cases {
            XCTAssertEqual(
                RelayRouting.mapToRelayPath(pathCase.path, agentId: pathCase.agent_id),
                pathCase.expect,
                "path \(pathCase.path)"
            )
        }
    }

    func testWebSocketURLTable() throws {
        let file = try loadFile()
        XCTAssertFalse(file.url_cases.isEmpty)
        for urlCase in file.url_cases {
            // The shared table names two URL shapes: "relay" rewrites the path
            // for an agent, "direct" takes the path as given (on iOS: the
            // channels the relay hosts itself, such as `ws/user-events`).
            let url: String
            switch urlCase.mode {
            case "relay":
                url = RelayRouting.agentWebSocketURLString(
                    relayServerURL: urlCase.base,
                    path: urlCase.path,
                    agentId: urlCase.agent_id,
                    token: urlCase.token
                )
            case "direct":
                url = RelayRouting.webSocketURLString(
                    relayServerURL: urlCase.base,
                    path: urlCase.path,
                    token: urlCase.token
                )
            default:
                return XCTFail("\(urlCase.name): unknown mode \(urlCase.mode)")
            }
            XCTAssertEqual(url, urlCase.expect, urlCase.name)
        }
    }

    func testAgentRESTBaseTable() throws {
        let file = try loadFile()
        // iOS reaches an agent only through the relay, so only the "relay"
        // rows of the shared table apply.
        let relayCases = file.rest_base_cases.filter { $0.mode == "relay" }
        XCTAssertFalse(relayCases.isEmpty)
        for baseCase in relayCases {
            let base = RelayRouting.agentRESTBaseURL(
                relayServerURL: baseCase.relay_url,
                agentId: baseCase.agent_id
            )
            XCTAssertEqual(base, baseCase.expect, baseCase.name)
        }
    }
}
