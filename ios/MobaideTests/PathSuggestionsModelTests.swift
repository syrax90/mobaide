import XCTest
@testable import Mobaide

/// `PathSuggestionsModel` over a real `PathSuggestionRepository` and `AgentAPI`
/// (stubbed transport): the capability gate, one listing per directory while
/// typing inside it, the truncated-listing `q` follow-up, and remembered
/// failures. The debounce is a `ManualSleeper`, released one `tick()` at a time.
@MainActor
final class PathSuggestionsModelTests: XCTestCase {
    private let sleeper = ManualSleeper()

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func version(fs: String?) -> String {
        let capabilities = fs.map { #"{"fs":\#($0)}"# } ?? "{}"
        return #"{"name":"mobaide-agent","server_version":"1.4.0","protocol_versions":[7],"min_client_version":1,"capabilities":\#(capabilities)}"#
    }

    private func listing(_ path: String, _ dirs: [String], truncated: Bool = false) -> String {
        let names = dirs.map { #""\#($0)""# }.joined(separator: ",")
        return #"{"path":"\#(path)","dirs":[\#(names)],"truncated":\#(truncated)}"#
    }

    /// A focused model whose capability probe has already answered.
    private func focusedModel(supported: Bool = true) async -> PathSuggestionsModel {
        await probedModel(fs: supported ? #"{"dirs":true}"# : nil) { $0.focusChanged(true) }
    }

    /// A model whose capability probe, started by `start`, has already answered.
    private func probedModel(
        fs: String?,
        start: (PathSuggestionsModel) -> Void
    ) async -> PathSuggestionsModel {
        StubURLProtocol.enqueue(status: 200, body: version(fs: fs))
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        let tokenStore = TokenStore(keychain: InMemoryKeychainStore())
        tokenStore.saveAgentUrl("http://agent.example.com")
        let model = PathSuggestionsModel(
            repository: PathSuggestionRepository(agentAPI: agentAPI, tokenStore: tokenStore),
            sleeper: sleeper
        )
        start(model)
        await waitUntil { StubURLProtocol.requests.count == 1 }
        await settle()
        return model
    }

    /// Lets queued main-actor work run, so a request the model decided to
    /// start has reached its debounce.
    private func settle() async {
        for _ in 0..<20 { await Task.yield() }
    }

    /// Releases the pending debounce and waits for the listing to be asked.
    private func releaseDebounce(expectingRequests count: Int) async {
        await waitUntil { sleeper.isWaiting }
        sleeper.tick()
        await waitUntil { StubURLProtocol.requests.count == count }
        await settle()
    }

    private func lastListingQuery() -> [String: String] {
        let url = StubURLProtocol.requests.last?.url
        let items = url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false)?.queryItems } ?? []
        return Dictionary(uniqueKeysWithValues: items.map { ($0.name, $0.value ?? "") })
    }

    func testListsADirectoryOnceAndFiltersItLocallyWhileTyping() async {
        let model = await focusedModel()
        StubURLProtocol.enqueue(status: 200, body: listing("/", [".hidden", "Users", "usr", "opt"]))

        model.textChanged("/")
        await releaseDebounce(expectingRequests: 2)
        await waitUntil { !model.names.isEmpty }

        XCTAssertEqual(StubURLProtocol.requests.last?.url?.path, "/api/fs/dirs")
        XCTAssertEqual(lastListingQuery(), ["path": "/"])
        XCTAssertEqual(sleeper.durations, [PathCompletion.debounce])
        XCTAssertEqual(model.names, ["Users", "usr", "opt"])

        model.textChanged("/us")
        model.textChanged("/Use")
        await settle()

        XCTAssertEqual(model.names, ["Users"])
        XCTAssertEqual(StubURLProtocol.requests.count, 2, "typing inside a listed directory must stay local")
        XCTAssertEqual(sleeper.durations.count, 1)
    }

    func testReAsksATruncatedListingWithTheTypedFragmentAsQ() async {
        let model = await focusedModel()
        StubURLProtocol.enqueue(status: 200, body: listing("/big", ["aaa"], truncated: true))
        StubURLProtocol.enqueue(status: 200, body: listing("/big", ["project"]))

        model.textChanged("/big/")
        await releaseDebounce(expectingRequests: 2)
        model.textChanged("/big/pro")
        await releaseDebounce(expectingRequests: 3)
        await waitUntil { model.names == ["project"] }

        XCTAssertEqual(lastListingQuery(), ["path": "/big/", "q": "pro"])
        XCTAssertEqual(model.names, ["project"])
    }

    func testShowsEightAndCountsTheRest() async {
        let model = await focusedModel()
        StubURLProtocol.enqueue(status: 200, body: listing("/", (0..<10).map { "d\($0)" }))

        model.textChanged("/")
        await releaseDebounce(expectingRequests: 2)
        await waitUntil { !model.names.isEmpty }

        XCTAssertEqual(model.names.count, 8)
        XCTAssertEqual(model.overflow, 2)
    }

    func testAcceptWritesTheAgentExpandedPathForATildeQuery() async {
        let model = await focusedModel()
        StubURLProtocol.enqueue(status: 200, body: listing("/home/me", ["code"]))

        model.textChanged("~/")
        await releaseDebounce(expectingRequests: 2)
        await waitUntil { !model.names.isEmpty }

        XCTAssertEqual(lastListingQuery(), ["path": "~/"])
        XCTAssertEqual(model.accept("code"), "/home/me/code/")
    }

    func testRemembersAMissingDirectorySoTypingUnderItStaysLocal() async {
        let model = await focusedModel()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"No such directory: /gone/"}"#)

        model.textChanged("/gone/")
        await releaseDebounce(expectingRequests: 2)
        model.textChanged("/gone/abc")
        await settle()

        XCTAssertEqual(sleeper.durations.count, 1)
        XCTAssertEqual(StubURLProtocol.requests.count, 2)
        XCTAssertEqual(model.names, [])
    }

    func testDoesNotRememberABusyAgentButAsksAgainOnlyOnceTheDirectoryChanges() async {
        let model = await focusedModel()
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"busy"}"#)

        model.textChanged("/srv/")
        await releaseDebounce(expectingRequests: 2)
        model.textChanged("/srv/x")
        await settle()
        XCTAssertEqual(sleeper.durations.count, 1, "a failed request is not retried on every keystroke")

        StubURLProtocol.enqueue(status: 200, body: listing("/", ["srv"]))
        model.textChanged("/")
        await releaseDebounce(expectingRequests: 3)
        StubURLProtocol.enqueue(status: 200, body: listing("/srv", ["app"]))
        model.textChanged("/srv/")
        await releaseDebounce(expectingRequests: 4)
        await waitUntil { model.names == ["app"] }

        XCTAssertEqual(lastListingQuery(), ["path": "/srv/"])
        XCTAssertEqual(model.names, ["app"])
    }

    func testAsksNothingOfAnAgentWithoutTheCapability() async {
        let model = await focusedModel(supported: false)

        model.textChanged("/")
        await settle()

        XCTAssertEqual(StubURLProtocol.requests.count, 1)
        XCTAssertTrue(sleeper.durations.isEmpty)
        XCTAssertEqual(model.names, [])
    }

    func testHidesTheListOnceTheFieldLosesFocus() async {
        let model = await focusedModel()
        StubURLProtocol.enqueue(status: 200, body: listing("/", ["srv"]))
        model.textChanged("/")
        await releaseDebounce(expectingRequests: 2)
        await waitUntil { !model.names.isEmpty }

        model.focusChanged(false)

        XCTAssertEqual(model.names, [])
    }

    func testProbeLearnsAWindowsHostBeforeFocus_andPicksUseItsSeparator() async {
        let model = await probedModel(fs: #"{"dirs":true,"windows_paths":true}"#) { $0.probe() }
        XCTAssertTrue(model.windowsPaths)

        StubURLProtocol.enqueue(status: 200, body: listing(#"C:\\"#, ["Users"]))
        model.focusChanged(true)
        model.textChanged(#"C:/"#)
        await releaseDebounce(expectingRequests: 2)

        XCTAssertEqual(model.names, ["Users"])
        XCTAssertEqual(model.separator, "\\")
        XCTAssertEqual(model.accept("Users"), #"C:\Users\"#)
    }

    func testAnOlderAgentReadsAsAPosixHost() async {
        let model = await focusedModel()

        XCTAssertFalse(model.windowsPaths)
    }
}
