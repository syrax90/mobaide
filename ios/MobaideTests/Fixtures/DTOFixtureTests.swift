import XCTest
@testable import Mobaide

final class DTOFixtureTests: XCTestCase {
    func testEveryDTOFixtureRoundTrips() throws {
        let urls = try FixtureLoader.dtoFixtureURLs()
        XCTAssertFalse(urls.isEmpty, "no DTO fixtures found in bundle")

        var coveredTypes = Set<String>()
        for url in urls {
            let file = try FixtureLoader.load(DTOFixtureFile.self, at: url)
            guard let runner = FixtureRegistry.all[file.type] else {
                XCTFail("fixture \(url.lastPathComponent) references unregistered type \(file.type)")
                continue
            }
            XCTAssertFalse(file.cases.isEmpty, "\(file.type) has no cases")
            coveredTypes.insert(file.type)
            for fixtureCase in file.cases {
                XCTAssertNoThrow(
                    try runner.run(file.type, fixtureCase),
                    "\(file.type).\(fixtureCase.name) failed"
                )
            }
        }

        let uncovered = Set(FixtureRegistry.all.keys).subtracting(coveredTypes)
        XCTAssertTrue(uncovered.isEmpty, "registered DTO types without fixtures: \(uncovered.sorted())")
    }

    func testUnknownSessionKindCoercesInsteadOfThrowing() throws {
        let decoded = try WireJSON.decoder().decode(SessionKind.self, from: Data("\"never_heard_of_it\"".utf8))
        XCTAssertEqual(decoded, .claude)
    }

    func testGitItemComputedPropertiesAreDerivedNotSerialized() throws {
        let withWorktree = GitItem(kind: GitItem.kindBranchWithWorktree, branch: "b", worktree_path: "/wt/b")
        XCTAssertEqual(withWorktree.id, "branch_with_worktree:b")
        XCTAssertTrue(withWorktree.isDeletable)

        let orphan = GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt/x")
        XCTAssertEqual(orphan.id, "orphan_worktree:/wt/x")

        let unknownKind = GitItem(kind: "future_kind", branch: "b", worktree_path: "/wt/b", is_current: true)
        XCTAssertEqual(unknownKind.id, "future_kind:b|/wt/b")
        XCTAssertFalse(unknownKind.isDeletable)

        let encoded = try WireJSON.encoder().encode(withWorktree)
        let tree = try WireJSON.decoder().decode(JSONValue.self, from: encoded)
        XCTAssertNil(tree["id"], "computed id must never serialize")
        XCTAssertNil(tree["isDeletable"], "computed isDeletable must never serialize")
    }

    func testVersionCapabilitiesHelpersNeverThrow() throws {
        let file = try FixtureLoader.load(DTOFixtureFile.self, path: "dto/agent/VersionInfoResponse.json")
        let fullCase = try XCTUnwrap(file.cases.first { $0.name == "full_heterogeneous_capabilities" })
        let data = try WireJSON.encoder().encode(fullCase.wire)
        let version = try WireJSON.decoder().decode(VersionInfoResponse.self, from: data)

        XCTAssertEqual(version.cliAvailable("claude"), true)
        XCTAssertEqual(version.cliAvailable("codex"), false)
        XCTAssertNil(version.cliAvailable("missing"))
        XCTAssertEqual(version.cliModels("claude"), ["opus", "sonnet"])
        XCTAssertEqual(version.cliModels("codex"), [])
        XCTAssertNil(version.cliModels("missing"))

        let empty = VersionInfoResponse(name: "x", server_version: "1", protocol_versions: [6], min_client_version: 6)
        XCTAssertNil(empty.cliAvailable("claude"))
        XCTAssertNil(empty.cliModels("claude"))
    }
}
