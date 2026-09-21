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

    func testUnknownSessionKindIsPreservedVerbatim() throws {
        // Proto-v7: no more unknown→claude coercion — an unrecognized kind
        // string is held verbatim as `.custom` and re-encodes byte-identical,
        // so custom/newer kinds never re-bucket into Claude.
        let decoded = try WireJSON.decoder().decode(SessionKind.self, from: Data("\"never_heard_of_it\"".utf8))
        XCTAssertEqual(decoded, .custom("never_heard_of_it"))

        let reencoded = try WireJSON.encoder().encode(decoded)
        XCTAssertEqual(String(decoding: reencoded, as: UTF8.self), "\"never_heard_of_it\"")
    }

    func testGitItemComputedPropertiesAreDerivedNotSerialized() throws {
        let withWorktree = GitItem(kind: GitItem.kindBranchWithWorktree, branch: "b", worktree_path: "/wt/b")
        XCTAssertEqual(withWorktree.id, "branch_with_worktree:b")
        XCTAssertTrue(withWorktree.isDeletable(deleteRemote: false))

        let orphan = GitItem(kind: GitItem.kindOrphanWorktree, worktree_path: "/wt/x")
        XCTAssertEqual(orphan.id, "orphan_worktree:/wt/x")

        let unknownKind = GitItem(kind: "future_kind", branch: "b", worktree_path: "/wt/b", is_current: true)
        XCTAssertEqual(unknownKind.id, "future_kind:b|/wt/b")
        XCTAssertFalse(unknownKind.isDeletable(deleteRemote: true))

        let encoded = try WireJSON.encoder().encode(withWorktree)
        let tree = try WireJSON.decoder().decode(JSONValue.self, from: encoded)
        XCTAssertNil(tree["id"], "computed id must never serialize")
        XCTAssertNil(tree["isDeletable"], "computed isDeletable must never serialize")
    }

    func testVersionCapabilitiesHelpersNeverThrow() throws {
        let version = try FixtureLoader.decodeCase(
            VersionInfoResponse.self,
            path: "dto/agent/VersionInfoResponse.json",
            name: "full_heterogeneous_capabilities"
        )

        XCTAssertEqual(version.cliAvailable("claude"), true)
        XCTAssertEqual(version.cliAvailable("codex"), false)
        XCTAssertNil(version.cliAvailable("missing"))
        XCTAssertEqual(version.cliModels("claude"), ["opus", "sonnet"])
        XCTAssertEqual(version.cliModels("codex"), [])
        XCTAssertNil(version.cliModels("missing"))
        XCTAssertEqual(
            version.cliModelDetails("codex"),
            [
                CliModelDetail(
                    id: "gpt-5.6-sol",
                    efforts: ["low", "medium", "high", "xhigh", "max", "ultra"],
                    defaultEffort: "low"
                ),
                CliModelDetail(id: "gpt-5.4-mini", efforts: ["low"], defaultEffort: nil),
            ]
        )
        XCTAssertNil(version.cliModelDetails("claude"))
        XCTAssertNil(version.cliModelDetails("missing"))
        XCTAssertTrue(version.cliModelsPending("antigravity"))
        XCTAssertFalse(version.cliModelsPending("codex"))
        XCTAssertFalse(version.cliModelsPending("missing"))

        let empty = VersionInfoResponse(name: "x", server_version: "1", protocol_versions: [6], min_client_version: 6)
        XCTAssertNil(empty.cliAvailable("claude"))
        XCTAssertNil(empty.cliModels("claude"))
        XCTAssertNil(empty.cliModelDetails("codex"))
        XCTAssertFalse(empty.cliModelsPending("codex"))
    }

    func testWireDataServesTheWireFormNotTheCanonicalOne() throws {
        // A stubbed agent must answer with the bytes as they travel, not with
        // the materialized defaults `decoded_canonical` pins after decoding.
        let data = try FixtureLoader.wireData(
            path: "dto/agent/VersionInfoResponse.json",
            case: "missing_capabilities_defaults_to_empty_object"
        )
        let tree = try WireJSON.decoder().decode(JSONValue.self, from: data)
        XCTAssertNil(tree["capabilities"], "wireData must not materialize decoded_canonical defaults")
        XCTAssertEqual(tree["name"], .string("central"))
    }

    func testCaseHelpersThrowCaseNotFoundForUnknownName() throws {
        XCTAssertThrowsError(
            try FixtureLoader.wireData(path: "dto/agent/VersionInfoResponse.json", case: "no_such_case")
        ) { error in
            guard case .caseNotFound(let name)? = error as? FixtureLoaderError else {
                return XCTFail("expected caseNotFound, got \(error)")
            }
            XCTAssertEqual(name, "dto/agent/VersionInfoResponse.json#no_such_case")
        }

        XCTAssertThrowsError(
            try FixtureLoader.decodeCase(
                VersionInfoResponse.self,
                path: "dto/agent/VersionInfoResponse.json",
                name: "no_such_case"
            )
        ) { error in
            guard case .caseNotFound? = error as? FixtureLoaderError else {
                return XCTFail("decodeCase must surface caseNotFound unchanged, got \(error)")
            }
        }
    }
}
