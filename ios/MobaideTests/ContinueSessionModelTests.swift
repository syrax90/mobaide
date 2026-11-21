import XCTest
@testable import Mobaide

/// Delete in "Continue a session": offered only on rows nothing has open when
/// the agent can delete, and on success the row leaves the list without a reload.
@MainActor
final class ContinueSessionModelTests: XCTestCase {
    private static let closedRow = #"{"session_id":"a","cwd":"/p","title":"Refactor the login flow","open_session_id":null,"open_elsewhere":false}"#
    private static let otherRow = #"{"session_id":"b","cwd":"/p","title":"Fix flaky upload test","open_session_id":null,"open_elsewhere":false}"#
    private static let openInMobaideRow = #"{"session_id":"c","cwd":"/p","open_session_id":"mob-7","open_elsewhere":false}"#
    private static let openElsewhereRow = #"{"session_id":"d","cwd":"/p","open_session_id":null,"open_elsewhere":true}"#

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func page(_ rows: [String], canDelete: Bool?) -> String {
        let flag = canDelete.map { #","can_delete":\#($0)"# } ?? ""
        return #"{"sessions":[\#(rows.joined(separator: ","))],"next_cursor":null\#(flag)}"#
    }

    private func loadedModel(_ rows: [String], canDelete: Bool?, kind: String = "claude") async -> ContinueSessionModel {
        StubURLProtocol.enqueue(status: 200, body: page(rows, canDelete: canDelete))
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        let model = ContinueSessionModel(agentAPI: agentAPI, kind: kind, folder: "/p")
        await model.reload()
        return model
    }

    func testDeleteIsOfferedOnlyOnClosedRowsOfAnAgentThatCanDelete() async throws {
        let model = await loadedModel(
            [Self.closedRow, Self.openInMobaideRow, Self.openElsewhereRow],
            canDelete: true
        )
        let rows = model.rows

        model.askDelete(rows[1])
        model.askDelete(rows[2])
        XCTAssertNil(model.pendingDelete)

        model.askDelete(rows[0])
        XCTAssertEqual(model.pendingDelete?.session_id, "a")

        let older = await loadedModel([Self.closedRow], canDelete: nil)
        older.askDelete(older.rows[0])
        XCTAssertNil(older.pendingDelete, "an agent without can_delete offers no Delete")
    }

    func testConfirmDeleteSendsTheDeleteAndRemovesTheRowInPlace() async throws {
        let model = await loadedModel([Self.closedRow, Self.otherRow], canDelete: true, kind: "codex")
        StubURLProtocol.enqueue(status: 204, body: "")

        model.askDelete(model.rows[0])
        await model.confirmDelete(model.rows[0])

        XCTAssertEqual(model.rows.map(\.session_id), ["b"])
        XCTAssertNil(model.pendingDelete)
        XCTAssertNil(model.deleteError)
        XCTAssertFalse(model.deleting)
        XCTAssertEqual(StubURLProtocol.requests.count, 2, "no reload after the delete")
        let request = try XCTUnwrap(StubURLProtocol.requests.last)
        XCTAssertEqual(request.httpMethod, "DELETE")
        XCTAssertEqual(request.url?.absoluteString, "http://agent.example.com/api/chat/codex/sessions/a")
    }

    func testAFailedDeleteKeepsTheRowAndShowsTheAgentDetail() async throws {
        let model = await loadedModel([Self.closedRow], canDelete: true)
        StubURLProtocol.enqueue(
            status: 409,
            body: #"{"detail":"This session is open in a Mobaide chat. Close the chat first."}"#
        )

        await model.confirmDelete(model.rows[0])

        XCTAssertEqual(model.rows.map(\.session_id), ["a"])
        XCTAssertEqual(model.deleteError, "This session is open in a Mobaide chat. Close the chat first.")
        XCTAssertFalse(model.deleting)
    }
}
