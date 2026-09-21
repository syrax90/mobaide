import XCTest
@testable import Mobaide

/// `GitConflictModel` over the real `AgentAPI` (stubbed transport): the file
/// seeds the editor once, a write carries the token the text was read under,
/// and the agent's `file_changed` / `not_conflicted` refusals never cost the
/// user the draft. The marker scan is driven through `rescan()`; its debounce
/// has no clock seam.
@MainActor
final class GitConflictModelTests: XCTestCase {
    private let path = "src/login.py"
    private let conflicted = "a\n<<<<<<< HEAD\nmain\n=======\ntopic\n>>>>>>> fedcba9 (Fix)\nz\n"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeModel() -> GitConflictModel {
        let client = HTTPClient(session: HTTPClient.makeSession(protocolClasses: [StubURLProtocol.self]))
        let agentAPI = AgentAPI(
            baseURL: { "http://agent.example.com" },
            tokens: FakeAgentTokens(token: AgentToken(value: "tok")),
            client: client
        )
        return GitConflictModel(sessionId: "s1", path: path, agentAPI: agentAPI)
    }

    private func conflictBody(
        content: String? = nil,
        token: String? = "t1",
        kind: String = GitConflictResponse.kindText,
        textless: Bool = false,
        byteLimit: Int = 262_144
    ) throws -> String {
        let response = GitConflictResponse(
            path: path, code: "UU", state: GitRepoInfo.stateRebasing, kind: kind,
            content: textless ? nil : (content ?? conflicted), token: textless ? nil : token,
            markers: 1, has_base: false,
            ours: GitConflictSide(label: "main", present: true),
            theirs: GitConflictSide(label: "fedcba9 Fix", present: true),
            byte_limit: byteLimit
        )
        return String(decoding: try WireJSON.encoder().encode(response), as: UTF8.self)
    }

    /// The file on screen, the load's traffic already forgotten.
    private func loadedModel() async throws -> GitConflictModel {
        let model = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try conflictBody())
        await model.load()
        StubURLProtocol.reset()
        return model
    }

    private func type(_ text: String, into model: GitConflictModel) {
        model.buffer.text = text
        model.rescan()
    }

    private func refusal(_ code: String, message: String? = "src/login.py") -> String {
        let quoted = message.map { "\"\($0)\"" } ?? "null"
        return #"{"detail":{"code":"\#(code)","message":\#(quoted)}}"#
    }

    private let ok = #"{"ok":true}"#

    private var requestLines: [String] {
        StubURLProtocol.requests.map { "\($0.httpMethod ?? "?") \($0.url?.absoluteString ?? "?")" }
    }

    private func gitURL(_ leaf: String) -> String { "http://agent.example.com/api/sessions/s1/git/\(leaf)" }

    private func sentBody<T: Decodable>(_ type: T.Type, at index: Int) throws -> T {
        let bodies = StubURLProtocol.bodies
        XCTAssertTrue(bodies.indices.contains(index), "no request body at \(index)")
        return try JSONDecoder().decode(type, from: bodies[index])
    }

    // MARK: - Load

    func testLoadSeedsTheEditorWithTheFileAndCountsItsMarkers() async throws {
        let model = makeModel()
        XCTAssertEqual(model.state, .loading)
        StubURLProtocol.enqueue(status: 200, body: try conflictBody())

        await model.load()

        XCTAssertEqual(requestLines, ["GET \(gitURL("conflict"))?path=src%2Flogin.py"])
        XCTAssertEqual(model.conflict?.token, "t1")
        XCTAssertEqual(model.buffer.text, conflicted)
        XCTAssertEqual(model.scan, ConflictScan(conflicts: 1, hasMarkers: true, hasBase: false))
        XCTAssertFalse(model.isDirty)
        XCTAssertFalse(model.isBusy)
        XCTAssertTrue(model.isEditable)
        XCTAssertFalse(model.canApply, "a marker line is left")
        XCTAssertFalse(model.canSaveDraft, "nothing was typed")
    }

    func testTypingRescansIntoADirtyDraftAndTypingTheFileBackIsCleanAgain() async throws {
        let model = try await loadedModel()

        type("a\nmerged\nz\n", into: model)

        XCTAssertEqual(model.scan, ConflictScan())
        XCTAssertTrue(model.isDirty)
        XCTAssertTrue(model.canApply)
        XCTAssertTrue(model.canSaveDraft)

        type(conflicted, into: model)

        XCTAssertFalse(model.isDirty)
        XCTAssertFalse(model.canSaveDraft)
    }

    /// SwiftUI re-runs the screen's `.task` when the pushed "View diff" pops.
    func testASecondLoadNeverReseedsTheEditorOverADraftButReloadFileStillDoes() async throws {
        let model = try await loadedModel()
        type("a\nhalf done\nz\n", into: model)
        StubURLProtocol.enqueue(status: 200, body: try conflictBody(content: "theirs now\n", token: "t2"))

        await model.load()

        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "the file is already on screen")
        XCTAssertEqual(model.buffer.text, "a\nhalf done\nz\n")
        XCTAssertTrue(model.isDirty)
        XCTAssertEqual(model.conflict?.token, "t1")

        await model.reloadFile()

        XCTAssertEqual(requestLines, ["GET \(gitURL("conflict"))?path=src%2Flogin.py"])
        XCTAssertEqual(model.buffer.text, "theirs now\n")
        XCTAssertFalse(model.isDirty)
        XCTAssertEqual(model.conflict?.token, "t2")
    }

    func testATextOverTheFilesByteLimitOffersNeitherSaveUntilItIsCutBackDown() async throws {
        let model = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try conflictBody(byteLimit: 1024))
        await model.load()
        StubURLProtocol.reset()

        // 600 characters in the editor, 1 200 bytes on the wire
        type(String(repeating: "é", count: 600), into: model)

        XCTAssertEqual(model.scan, ConflictScan(tooLarge: true))
        XCTAssertTrue(model.isDirty)
        XCTAssertFalse(model.canApply)
        XCTAssertFalse(model.canSaveDraft)

        type(String(repeating: "é", count: 512), into: model)

        XCTAssertEqual(model.scan, ConflictScan())
        XCTAssertTrue(model.canApply)
        XCTAssertTrue(model.canSaveDraft)
    }

    func testAFileThatIsNotTextIsReadyWithoutAnEditorOrASave() async throws {
        let model = makeModel()
        StubURLProtocol.enqueue(status: 200, body: try conflictBody(kind: GitConflictResponse.kindBinary, textless: true))
        await model.load()
        StubURLProtocol.reset()

        XCTAssertEqual(model.conflict?.kind, GitConflictResponse.kindBinary)
        XCTAssertFalse(model.isEditable)
        XCTAssertFalse(model.canApply)
        XCTAssertEqual(model.buffer.text, "")

        await model.apply()

        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "there is no token to write under")
    }

    func testLoadFailuresMapToTheirStates() async throws {
        let outdated = makeModel()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Not Found"}"#)
        await outdated.load()
        XCTAssertEqual(outdated.state, .agentOutdated)

        let gone = makeModel()
        StubURLProtocol.enqueue(status: 404, body: #"{"detail":"Session 's1' not found"}"#)
        await gone.load()
        XCTAssertEqual(gone.state, .sessionGone)

        let failing = makeModel()
        StubURLProtocol.enqueue(status: 500, body: #"{"detail":{"code":"git_error","message":"boom"}}"#)
        await failing.load()
        XCTAssertEqual(
            failing.state,
            .error(SessionGitCopy.errorMessage(for: SessionGitError.git(code: SessionGitError.codeGitError, message: "boom")))
        )
        XCTAssertNil(failing.outcome)
    }

    func testAFileResolvedBeforeItCouldBeReadClosesWithTheReason() async throws {
        let model = makeModel()
        StubURLProtocol.enqueue(status: 409, body: refusal(SessionGitError.codeNotConflicted))

        await model.load()

        XCTAssertEqual(model.outcome, GitConflictModel.Outcome(message: "login.py is no longer in conflict."))
    }

    // MARK: - Apply and draft

    func testApplySendsTheTextUnderTheTokenItWasReadWithAndClosesWithTheOutcome() async throws {
        let model = try await loadedModel()
        type("a\nmerged\nz\n", into: model)
        StubURLProtocol.enqueue(status: 200, body: ok)

        await model.apply()

        XCTAssertEqual(requestLines, ["POST \(gitURL("conflict/resolve"))"])
        XCTAssertEqual(
            try sentBody(GitConflictResolveRequest.self, at: 0),
            GitConflictResolveRequest(path: path, token: "t1", content: "a\nmerged\nz\n", mark_resolved: true)
        )
        XCTAssertEqual(model.outcome, GitConflictModel.Outcome(message: "Resolved src/login.py."))
        XCTAssertFalse(model.isBusy)
    }

    func testSaveDraftKeepsTheTextAdoptsTheFilesNewTokenAndSaysSo() async throws {
        let draft = "a\n<<<<<<< HEAD\nhalf\n=======\ntopic\n>>>>>>> x\nz\n"
        let model = try await loadedModel()
        type(draft, into: model)
        StubURLProtocol.enqueue(status: 200, body: ok)
        StubURLProtocol.enqueue(status: 200, body: try conflictBody(content: draft, token: "t2"))

        await model.saveDraft()

        XCTAssertEqual(requestLines, ["POST \(gitURL("conflict/resolve"))", "GET \(gitURL("conflict"))?path=src%2Flogin.py"])
        XCTAssertEqual(
            try sentBody(GitConflictResolveRequest.self, at: 0),
            GitConflictResolveRequest(path: path, token: "t1", content: draft, mark_resolved: false)
        )
        XCTAssertNil(model.outcome)
        XCTAssertEqual(model.buffer.text, draft)
        XCTAssertFalse(model.isDirty)
        XCTAssertFalse(model.overwriteWarning)
        XCTAssertEqual(model.toast?.text, "Saved src/login.py — the conflict is still open.")
        XCTAssertEqual(model.toast?.tone, .ok)

        StubURLProtocol.reset()
        type("a\nmerged\nz\n", into: model)
        StubURLProtocol.enqueue(status: 200, body: ok)
        await model.apply()

        XCTAssertEqual(try sentBody(GitConflictResolveRequest.self, at: 0).token, "t2")
    }

    func testSaveDraftWhenTheFileReadBackIsNotTheDraftKeepsTheDraftAndWarnsBeforeApplyOverwrites() async throws {
        let draft = "a\n<<<<<<< HEAD\nhalf\n=======\ntopic\n>>>>>>> x\nz\n"
        let model = try await loadedModel()
        type(draft, into: model)
        StubURLProtocol.enqueue(status: 200, body: ok)
        // Someone wrote the file between the save and the read that follows it.
        StubURLProtocol.enqueue(status: 200, body: try conflictBody(content: "a\nsomeone else\nz\n", token: "t2"))

        await model.saveDraft()

        XCTAssertEqual(model.buffer.text, draft)
        XCTAssertTrue(model.overwriteWarning)
        XCTAssertFalse(model.isStale)
        XCTAssertFalse(model.isDirty)

        StubURLProtocol.reset()
        type("a\nmerged\nz\n", into: model)
        StubURLProtocol.enqueue(status: 200, body: ok)
        await model.apply()

        XCTAssertEqual(
            try sentBody(GitConflictResolveRequest.self, at: 0),
            GitConflictResolveRequest(path: path, token: "t2", content: "a\nmerged\nz\n", mark_resolved: true)
        )
    }

    func testAnyOtherRefusalIsAToastAndTheDraftStays() async throws {
        let model = try await loadedModel()
        type("mine\n", into: model)
        StubURLProtocol.enqueue(status: 400, body: #"{"detail":{"code":"too_large"}}"#)

        await model.apply()

        XCTAssertEqual(model.toast?.text, "The text is too large to save from here.")
        XCTAssertEqual(model.toast?.tone, .danger)
        XCTAssertNil(model.outcome)
        XCTAssertFalse(model.isStale)
        XCTAssertFalse(model.isGone)
        XCTAssertFalse(model.isBusy)
        XCTAssertEqual(model.buffer.text, "mine\n")
    }

    // MARK: - The file moved on

    private func staleModel() async throws -> GitConflictModel {
        let model = try await loadedModel()
        type("mine\n", into: model)
        StubURLProtocol.enqueue(status: 409, body: refusal(SessionGitError.codeFileChanged))
        await model.apply()
        StubURLProtocol.reset()
        return model
    }

    func testAFileThatMovedOnRaisesTheStaleFlagAndKeepsTheDraft() async throws {
        let model = try await staleModel()

        XCTAssertTrue(model.isStale)
        XCTAssertNil(model.outcome)
        XCTAssertNil(model.toast)
        XCTAssertEqual(model.buffer.text, "mine\n")
        XCTAssertTrue(model.isDirty)
    }

    func testReloadFileReplacesTheDraftWithTheFileAsItIsNow() async throws {
        let model = try await staleModel()
        StubURLProtocol.enqueue(status: 200, body: try conflictBody(content: "theirs now\n", token: "t2"))

        await model.reloadFile()

        XCTAssertEqual(model.buffer.text, "theirs now\n")
        XCTAssertFalse(model.isStale)
        XCTAssertFalse(model.isDirty)
        XCTAssertFalse(model.overwriteWarning)
        XCTAssertEqual(model.scan, ConflictScan())
    }

    func testKeepMyEditsKeepsTheDraftWarnsAndAppliesOverTheNewerFile() async throws {
        let model = try await staleModel()
        StubURLProtocol.enqueue(status: 200, body: try conflictBody(content: "theirs now\n", token: "t2"))

        await model.keepMyEdits()

        XCTAssertEqual(model.buffer.text, "mine\n")
        XCTAssertFalse(model.isStale)
        XCTAssertTrue(model.overwriteWarning)
        XCTAssertTrue(model.isDirty)

        StubURLProtocol.reset()
        StubURLProtocol.enqueue(status: 200, body: ok)
        await model.apply()

        XCTAssertEqual(
            try sentBody(GitConflictResolveRequest.self, at: 0),
            GitConflictResolveRequest(path: path, token: "t2", content: "mine\n", mark_resolved: true)
        )
    }

    func testAFailedReloadNeverCostsTheDraft() async throws {
        let model = try await staleModel()
        StubURLProtocol.enqueue(status: 503, body: #"{"detail":"Agent is offline"}"#)

        await model.keepMyEdits()

        XCTAssertEqual(model.buffer.text, "mine\n")
        XCTAssertNotNil(model.conflict, "the editor stays on screen")
        XCTAssertEqual(model.toast?.tone, .danger)
        XCTAssertFalse(model.isBusy)
    }

    // MARK: - The conflict is gone

    func testAFileNoLongerInConflictStopsTheSavesButKeepsTheDraft() async throws {
        let model = try await loadedModel()
        type("mine\n", into: model)
        StubURLProtocol.enqueue(status: 409, body: refusal(SessionGitError.codeNotConflicted))

        await model.apply()

        XCTAssertTrue(model.isGone)
        XCTAssertFalse(model.canApply)
        XCTAssertFalse(model.canSaveDraft)
        XCTAssertNil(model.outcome, "the draft may be the only copy of the work: the user closes")
        XCTAssertEqual(model.buffer.text, "mine\n")
    }

    func testAReloadThatFindsTheConflictGoneKeepsTheDraftAndSaysSo() async throws {
        let model = try await staleModel()
        StubURLProtocol.enqueue(status: 409, body: refusal(SessionGitError.codeNotConflicted))

        await model.reloadFile()

        XCTAssertTrue(model.isGone)
        XCTAssertNil(model.outcome)
        XCTAssertEqual(model.buffer.text, "mine\n")
    }

    // MARK: - Whole-file picks and start over

    func testTakeSendsTheChosenSideAndClosesWithTheOutcome() async throws {
        let model = try await loadedModel()
        let theirs = try XCTUnwrap(
            GitConflictDisplay.takeChoices(try XCTUnwrap(model.conflict)).first { $0.side == GitConflictResponse.sideTheirs }
        )
        StubURLProtocol.enqueue(status: 200, body: ok)

        await model.take(theirs)

        XCTAssertEqual(requestLines, ["POST \(gitURL("conflict/take"))"])
        XCTAssertEqual(try sentBody(GitConflictTakeRequest.self, at: 0), GitConflictTakeRequest(path: path, side: "theirs"))
        XCTAssertEqual(model.outcome, GitConflictModel.Outcome(message: "Resolved src/login.py."))
    }

    func testStartOverRebuildsUnderTheOpenFilesTokenAndReseedsTheEditor() async throws {
        let rebuilt = "a\n<<<<<<< main\nmain\n||||||| base\norig\n=======\ntopic\n>>>>>>> fedcba9 Fix\nz\n"
        let model = try await loadedModel()
        type("half done\n", into: model)
        StubURLProtocol.enqueue(status: 200, body: ok)
        StubURLProtocol.enqueue(status: 200, body: try conflictBody(content: rebuilt, token: "t2"))

        await model.startOver(withOriginalLines: true)

        XCTAssertEqual(requestLines, ["POST \(gitURL("conflict/restore"))", "GET \(gitURL("conflict"))?path=src%2Flogin.py"])
        XCTAssertEqual(
            try sentBody(GitConflictRestoreRequest.self, at: 0),
            GitConflictRestoreRequest(path: path, style: GitConflictRestoreRequest.styleDiff3, token: "t1")
        )
        XCTAssertEqual(model.buffer.text, rebuilt)
        XCTAssertEqual(model.scan, ConflictScan(conflicts: 1, hasMarkers: true, hasBase: true))
        XCTAssertFalse(model.isDirty)
        XCTAssertNil(model.outcome)
    }

    func testStartOverWithoutOriginalLinesAsksForTheMergeStyle() async throws {
        let model = try await loadedModel()
        StubURLProtocol.enqueue(status: 200, body: ok)
        StubURLProtocol.enqueue(status: 200, body: try conflictBody())

        await model.startOver(withOriginalLines: false)

        XCTAssertEqual(try sentBody(GitConflictRestoreRequest.self, at: 0).style, GitConflictRestoreRequest.styleMerge)
    }

    func testStartOverRefusedBecauseTheFileMovedOnKeepsTheDraftAndRaisesTheStaleFlag() async throws {
        let model = try await loadedModel()
        type("half done\n", into: model)
        StubURLProtocol.enqueue(status: 409, body: refusal(SessionGitError.codeFileChanged))

        await model.startOver(withOriginalLines: false)

        XCTAssertTrue(model.isStale)
        XCTAssertEqual(model.buffer.text, "half done\n")
        XCTAssertEqual(requestLines, ["POST \(gitURL("conflict/restore"))"], "no reload behind the user's back")
    }
}
