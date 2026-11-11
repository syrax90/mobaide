import XCTest
@testable import Mobaide

@MainActor
final class TemplatesModelTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    func testToggleFavoriteSendsOnlyIsFavoriteViaPatch() async throws {
        let repo = AgentHarness().templatesRepository
        await repo.applyUpserted(makeTemplate(id: "t1", name: "T", favorite: false))
        let model = TemplatesModel(repository: repo)

        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"template_id":"t1","name":"T","body":"echo hi","is_favorite":true,"created_at":"t","updated_at":"t","version":2}"#
        )
        model.toggleFavorite(model.templates[0])
        await waitUntil { !StubURLProtocol.requests.isEmpty }

        XCTAssertEqual(StubURLProtocol.requests.first?.httpMethod, "PATCH")
        let sent = try WireJSON.decoder().decode(UpdateTemplateRequest.self, from: StubURLProtocol.bodies[0])
        XCTAssertEqual(sent.is_favorite, true)
        XCTAssertNil(sent.name)
        XCTAssertNil(sent.body)
    }

    func testSaveSuccessDismissesEditor() async {
        let model = TemplatesModel(repository: AgentHarness().templatesRepository)
        model.startCreate()
        XCTAssertNotNil(model.editorTarget)
        StubURLProtocol.enqueue(
            status: 200,
            body: #"{"template_id":"t1","name":"N","body":"b","is_favorite":false,"created_at":"t","updated_at":"t","version":1}"#
        )
        model.saveFromEditor(name: "N", body: "b")
        await waitUntil { model.editorTarget == nil }
        XCTAssertNil(model.editorTarget)
        XCTAssertNil(model.message)
    }

    func testSaveFailureKeepsEditorOpenWithDraft() async {
        let model = TemplatesModel(repository: AgentHarness().templatesRepository)
        model.startCreate()
        StubURLProtocol.enqueue(status: 500, body: "boom")
        model.saveFromEditor(name: "N", body: "b")
        await waitUntil { model.message != nil }
        XCTAssertNotNil(model.editorTarget)  // editor stays open → draft preserved
        XCTAssertNotNil(model.message)
    }
}
