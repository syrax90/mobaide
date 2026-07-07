import XCTest
@testable import Mobaide

@MainActor
final class ProjectFormModelTests: XCTestCase {
    private struct StubAgentTokens: AgentTokenProviding {
        func validToken() async -> AgentToken? { nil }
        func refreshedToken() async -> String? { nil }
    }

    private func makeRepo() -> ProjectsRepository {
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let bus = AgentEventBus(
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: FakeWebSocketTransportFactory(), sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore
        )
        let agentAPI = AgentAPI(baseURL: { "https://agent.test" }, tokens: StubAgentTokens())
        return ProjectsRepository(agentAPI: agentAPI, agentEventBus: bus)
    }

    private func wireObject(_ body: some Encodable) throws -> [String: JSONValue] {
        let data = try WireJSON.encoder().encode(body)
        let json = try WireJSON.decoder().decode(JSONValue.self, from: data)
        return json.objectValue ?? [:]
    }

    // MARK: - Tri-state PUT body

    func testUpdateBodyOmitsUnchangedFields() throws {
        let project = ProjectInfo(name: "p", path: "/p", conda_env: "ml")
        let model = ProjectFormModel(mode: .edit(project), repository: makeRepo())

        let body = model.toUpdateBody()
        XCTAssertNil(body.name)
        XCTAssertNil(body.path)
        XCTAssertNil(body.conda_env)
        XCTAssertNil(body.linear_api_key)
        XCTAssertNil(body.default_flags)
        XCTAssertNil(body.auto_scan_filter)

        let object = try wireObject(body)
        XCTAssertTrue(object.isEmpty, "an untouched edit encodes to {} (every key absent = keep)")
    }

    func testUpdateBodySetsChangedValuesAndClearsWithEmptyString() throws {
        let project = ProjectInfo(name: "p", path: "/p", conda_env: "ml")
        let model = ProjectFormModel(mode: .edit(project), repository: makeRepo())

        model.name = "p2"          // changed → value
        model.condaEnv = ""        // emptied → clear ("")
        model.clearLinearKey = true // explicit clear → ""

        let body = model.toUpdateBody()
        XCTAssertEqual(body.name, "p2")
        XCTAssertEqual(body.conda_env, "")
        XCTAssertEqual(body.linear_api_key, "")
        XCTAssertNil(body.path, "unchanged path stays absent")

        let object = try wireObject(body)
        XCTAssertEqual(object, ["name": "p2", "conda_env": "", "linear_api_key": ""])
    }

    func testUpdateBodyClearsFlagsWithEmptyObject() throws {
        let project = ProjectInfo(name: "p", path: "/p", default_flags: ["plan_mode": true])
        let model = ProjectFormModel(mode: .edit(project), repository: makeRepo())

        model.flags.removeAll()
        let body = model.toUpdateBody()
        XCTAssertEqual(body.default_flags, [:])

        let object = try wireObject(body)
        XCTAssertEqual(object, ["default_flags": .object([:])])
    }

    func testUpdateBodyReplacesFlags() throws {
        let project = ProjectInfo(name: "p", path: "/p")
        let model = ProjectFormModel(mode: .edit(project), repository: makeRepo())

        model.flags = [
            FlagEntry(key: "a", kind: .number, textValue: "1"),
            FlagEntry(key: "b", kind: .boolean, textValue: "true"),
        ]
        let body = model.toUpdateBody()
        XCTAssertEqual(body.default_flags, ["a": 1, "b": true])
    }

    func testHugeIntegralFlagValueDoesNotTrapOnEdit() {
        // A default_flags value beyond Int range decodes as .double; opening the
        // edit form must not trap converting it back to display text.
        let project = ProjectInfo(name: "p", path: "/p", default_flags: ["big": .double(1e19)])
        let model = ProjectFormModel(mode: .edit(project), repository: makeRepo())

        XCTAssertEqual(model.flags.count, 1)
        XCTAssertEqual(model.flags.first?.kind, .number)
        XCTAssertEqual(model.flags.first?.textValue, "10000000000000000000")
        XCTAssertNotNil(model.flags.first?.toJSONValue())
    }

    // MARK: - Create body

    func testCreateBodyTrimsNamePathAndAlwaysSerializesFlags() throws {
        let model = ProjectFormModel(mode: .create, repository: makeRepo())
        model.name = "  p  "
        model.path = "  /x  "
        model.linearApiKey = ""
        model.condaEnv = ""

        let body = model.toCreateBody()
        XCTAssertEqual(body.name, "p")
        XCTAssertEqual(body.path, "/x")
        XCTAssertNil(body.linear_api_key)
        XCTAssertNil(body.conda_env)
        XCTAssertEqual(body.default_flags, [:])
        XCTAssertNotNil(body.auto_scan_filter)

        let object = try wireObject(body)
        XCTAssertEqual(object["default_flags"], .object([:]), "empty default_flags is always serialized on create")
        XCTAssertNil(object["linear_api_key"], "a blank key is omitted, never sent as null")
    }

    // MARK: - Validation

    func testInvalidNameBlocksSave() {
        let model = ProjectFormModel(mode: .create, repository: makeRepo())
        model.path = "/x"
        model.name = "bad/name"
        XCTAssertEqual(model.nameError, "Use letters, digits, spaces, _-.")
        XCTAssertFalse(model.canSave)

        model.name = "Good name_1.2-3"
        XCTAssertNil(model.nameError)
        XCTAssertTrue(model.canSave)
    }

    func testDuplicateFlagKeyBlocksSave() {
        let model = ProjectFormModel(mode: .create, repository: makeRepo())
        model.name = "p"
        model.path = "/x"
        model.flags = [
            FlagEntry(key: "dup", kind: .string, textValue: "a"),
            FlagEntry(key: "dup", kind: .string, textValue: "b"),
        ]
        XCTAssertNil(model.flagKeyError(at: 0))
        XCTAssertEqual(model.flagKeyError(at: 1), "Duplicate key")
        XCTAssertFalse(model.canSave)
    }

    func testFlagValueTypeValidation() {
        let model = ProjectFormModel(mode: .create, repository: makeRepo())
        model.name = "p"
        model.path = "/x"
        model.flags = [
            FlagEntry(key: "flag", kind: .boolean, textValue: "yes"),
        ]
        XCTAssertEqual(model.flagValueError(at: 0), "Expected true/false")
        XCTAssertFalse(model.canSave)

        model.flags[0].kind = .number
        model.flags[0].textValue = "not-a-number"
        XCTAssertEqual(model.flagValueError(at: 0), "Not a number")

        model.flags[0].textValue = "42"
        XCTAssertNil(model.flagValueError(at: 0))
        XCTAssertTrue(model.canSave)
    }

    func testEmptyFilterSelectionBlocksSave() {
        let model = ProjectFormModel(mode: .create, repository: makeRepo())
        model.name = "p"
        model.path = "/x"
        XCTAssertTrue(model.canSave, "defaults satisfy the filter")

        model.triggerLabels.removeAll()
        XCTAssertFalse(model.filterValid)
        XCTAssertFalse(model.canSave)
    }
}
