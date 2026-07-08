import XCTest
@testable import Mobaide

@MainActor
final class TemplatesRepositoryTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeRepo() -> TemplatesRepository {
        AgentHarness().templatesRepository
    }

    private func templateJSON(id: String, name: String, favorite: Bool, version: Int = 1) -> String {
        #"""
        {"template_id":"\#(id)","name":"\#(name)","body":"b","is_favorite":\#(favorite),"created_at":"t","updated_at":"t","version":\#(version)}
        """#
    }

    func testRefreshReplacesCache() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: "[\(templateJSON(id: "t1", name: "A", favorite: false))]")
        try await repo.refreshTemplates()
        XCTAssertEqual(repo.currentTemplates.map(\.template_id), ["t1"])
    }

    func testApplyUpsertedInsertsThenReplacesById() async {
        let repo = makeRepo()
        await repo.applyUpserted(makeTemplate(id: "t1", name: "One", favorite: false))
        XCTAssertEqual(repo.currentTemplates.count, 1)

        await repo.applyUpserted(makeTemplate(id: "t1", name: "One-edited", favorite: true, version: 2))
        XCTAssertEqual(repo.currentTemplates.count, 1)
        XCTAssertEqual(repo.currentTemplates.first?.name, "One-edited")
        XCTAssertEqual(repo.currentTemplates.first?.is_favorite, true)
    }

    func testApplyDeletedRemovesById() async {
        let repo = makeRepo()
        await repo.applyUpserted(makeTemplate(id: "t1", name: "One", favorite: false))
        await repo.applyUpserted(makeTemplate(id: "t2", name: "Two", favorite: false))
        await repo.applyDeleted(templateId: "t1")
        XCTAssertEqual(repo.currentTemplates.map(\.template_id), ["t2"])
    }

    func testCreateAppliesServerReturnedRow() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: templateJSON(id: "t1", name: "Made", favorite: false))
        let created = try await repo.createTemplate(name: "Made", body: "b")
        XCTAssertEqual(created.template_id, "t1")
        XCTAssertEqual(repo.currentTemplates.map(\.template_id), ["t1"])
    }

    func testUpdateSendsPatchAndAppliesResult() async throws {
        let repo = makeRepo()
        StubURLProtocol.enqueue(status: 200, body: templateJSON(id: "t1", name: "A", favorite: true, version: 2))
        _ = try await repo.updateTemplate(templateId: "t1", update: UpdateTemplateRequest(is_favorite: true))
        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "PATCH")
        XCTAssertEqual(repo.currentTemplates.first?.is_favorite, true)
    }

    func testDeleteRemovesFromCache() async throws {
        let repo = makeRepo()
        await repo.applyUpserted(makeTemplate(id: "t1", name: "One", favorite: false))
        StubURLProtocol.enqueue(status: 204, body: "")
        try await repo.deleteTemplate(templateId: "t1")
        XCTAssertTrue(repo.currentTemplates.isEmpty)
    }
}
