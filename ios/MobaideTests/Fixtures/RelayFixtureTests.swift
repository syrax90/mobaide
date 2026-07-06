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
            let mode = try XCTUnwrap(ConnectionMode(rawValue: urlCase.mode), urlCase.name)
            let url = RelayRouting.webSocketURLString(
                baseURL: urlCase.base,
                path: urlCase.path,
                mode: mode,
                agentId: urlCase.agent_id,
                token: urlCase.token
            )
            XCTAssertEqual(url, urlCase.expect, urlCase.name)
        }
    }

    func testAgentRESTBaseTable() throws {
        let file = try loadFile()
        XCTAssertFalse(file.rest_base_cases.isEmpty)
        for baseCase in file.rest_base_cases {
            let mode = try XCTUnwrap(ConnectionMode(rawValue: baseCase.mode), baseCase.name)
            let base = RelayRouting.agentRESTBaseURL(
                mode: mode,
                agentURL: baseCase.agent_url,
                relayServerURL: baseCase.relay_url,
                agentId: baseCase.agent_id
            )
            XCTAssertEqual(base, baseCase.expect, baseCase.name)
        }
    }
}
