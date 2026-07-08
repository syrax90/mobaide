import XCTest
@testable import Mobaide

@MainActor
final class TemplatesModelTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    func testFavoritesAndAllGroupingSortedCaseInsensitively() async {
        let repo = AgentHarness().templatesRepository
        await repo.applyUpserted(makeTemplate(id: "1", name: "beta", favorite: true))
        await repo.applyUpserted(makeTemplate(id: "2", name: "Alpha", favorite: true))
        await repo.applyUpserted(makeTemplate(id: "3", name: "zed", favorite: false))
        await repo.applyUpserted(makeTemplate(id: "4", name: "Mid", favorite: false))

        let model = TemplatesModel(repository: repo)
        XCTAssertEqual(model.favorites.map(\.name), ["Alpha", "beta"])
        XCTAssertEqual(model.others.map(\.name), ["Mid", "zed"])
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

    func testEditorValidationRule() {
        // Name is trimmed; body is checked verbatim (a space counts as content).
        XCTAssertFalse(CreateOrEditTemplateSheet.isSaveEnabled(name: "  ", body: "x"))
        XCTAssertFalse(CreateOrEditTemplateSheet.isSaveEnabled(name: "n", body: ""))
        XCTAssertTrue(CreateOrEditTemplateSheet.isSaveEnabled(name: "n", body: " "))
        XCTAssertTrue(CreateOrEditTemplateSheet.isSaveEnabled(name: "  n  ", body: "x"))
    }
}
