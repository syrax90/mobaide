import XCTest
@testable import Mobaide

/// The 404 discriminator and the object-shaped `detail` parser behind the
/// session-scoped git screens, plus the copy they resolve to. The model flows
/// (`GitChangesModelTests`, `GitDiffModelTests`) exercise them end to end;
/// these pin the classification edges directly.
final class SessionGitErrorsTests: XCTestCase {
    private func mapped(_ error: any Error) -> SessionGitError? {
        SessionGitError.map(error) as? SessionGitError
    }

    // MARK: - map(_:)

    func testSessionNotFound404MapsToSessionGone() {
        XCTAssertEqual(
            mapped(APIError(code: 404, errorBody: #"{"detail":"Session 's1' not found"}"#)),
            .sessionGone
        )
    }

    func testEveryOther404MapsToAgentOutdated() {
        // The relay's unknown-route answer, FastAPI's default, and a body
        // that is not JSON at all — none of them is the session lookup miss.
        XCTAssertEqual(mapped(APIError(code: 404, errorBody: #"{"detail":"Unknown proxy route"}"#)), .agentOutdated)
        XCTAssertEqual(mapped(APIError(code: 404, errorBody: #"{"detail":"Not Found"}"#)), .agentOutdated)
        XCTAssertEqual(mapped(APIError(code: 404, errorBody: "")), .agentOutdated)
        XCTAssertEqual(
            mapped(APIError(code: 404, errorBody: #"{"detail":"Session 's1' not found, really"}"#)),
            .agentOutdated,
            "the suffix is part of the discriminator, not just the prefix"
        )
    }

    func testObjectDetailMapsToGitWithCodeAndOptionalMessage() {
        XCTAssertEqual(
            mapped(APIError(code: 503, errorBody: #"{"detail":{"code":"git_unavailable","message":null}}"#)),
            .git(code: "git_unavailable", message: nil)
        )
        XCTAssertEqual(
            mapped(APIError(code: 500, errorBody: #"{"detail":{"code":"git_error","message":"fatal: bad object"}}"#)),
            .git(code: "git_error", message: "fatal: bad object")
        )
    }

    func testUnrelatedAPIErrorPassesThroughUnchanged() {
        let expired = APIError(code: 401, errorBody: #"{"detail":"Session expired"}"#)
        XCTAssertEqual(SessionGitError.map(expired) as? APIError, expired)
        XCTAssertNil(mapped(expired))

        let conflict = APIError(code: 409, errorBody: #"{"detail":"busy"}"#)
        XCTAssertEqual(SessionGitError.map(conflict) as? APIError, conflict, "a string detail on a non-404 is not a git error")
    }

    func testTransportAndDecodeFailuresMapToNetwork() {
        guard case .network? = mapped(URLError(.notConnectedToInternet)) else {
            return XCTFail("URLError must map to .network")
        }
        let decoding = DecodingError.dataCorrupted(DecodingError.Context(codingPath: [], debugDescription: "bad"))
        guard case .network? = mapped(decoding) else {
            return XCTFail("DecodingError must map to .network")
        }
    }

    func testAlreadyMappedErrorIsReturnedAsIs() {
        XCTAssertEqual(mapped(SessionGitError.sessionGone), .sessionGone)
        XCTAssertEqual(mapped(SessionGitError.git(code: "x", message: nil)), .git(code: "x", message: nil))
    }

    func testUnknownErrorsPassThrough() {
        struct Other: Error {}
        XCTAssertTrue(SessionGitError.map(Other()) is Other)
    }

    // MARK: - isCancellation(_:)

    func testCancellationIsRecognisedForBothShapes() {
        XCTAssertTrue(SessionGitError.isCancellation(CancellationError()))
        XCTAssertTrue(SessionGitError.isCancellation(URLError(.cancelled)))
        XCTAssertFalse(SessionGitError.isCancellation(URLError(.timedOut)))
        XCTAssertFalse(SessionGitError.isCancellation(SessionGitError.sessionGone))
    }

    // MARK: - SessionGitDetail.parse

    func testDetailParseRequiresAStringCode() {
        XCTAssertEqual(
            SessionGitDetail.parse(#"{"detail":{"code":"git_timeout"}}"#),
            SessionGitDetail.Detail(code: "git_timeout", message: nil)
        )
        XCTAssertEqual(
            SessionGitDetail.parse(#"{"detail":{"code":"git_error","message":"boom"}}"#),
            SessionGitDetail.Detail(code: "git_error", message: "boom")
        )
        XCTAssertNil(SessionGitDetail.parse(#"{"detail":"Session 's1' not found"}"#), "string detail is the other shape")
        XCTAssertNil(SessionGitDetail.parse(#"{"detail":{"message":"no code"}}"#))
        XCTAssertNil(SessionGitDetail.parse(#"{"detail":{"code":7}}"#), "a non-string code is not coerced")
        XCTAssertNil(SessionGitDetail.parse("boom"))
        XCTAssertNil(SessionGitDetail.parse(""))
    }

    // MARK: - SessionGitCopy

    func testErrorMessageForEachGitCode() {
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: SessionGitError.git(code: SessionGitError.codeGitUnavailable, message: nil)),
            "git is not installed on the agent host."
        )
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: SessionGitError.git(code: SessionGitError.codeGitTimeout, message: "ignored")),
            "git took too long to answer. Try again."
        )
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: SessionGitError.git(code: SessionGitError.codeGitError, message: "fatal: bad object")),
            "git failed: fatal: bad object"
        )
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: SessionGitError.git(code: SessionGitError.codeGitError, message: nil)),
            "git failed unexpectedly."
        )
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: SessionGitError.git(code: GitStatusResponse.unavailableUnsafeDirectory, message: nil)),
            SessionGitCopy.unavailable(GitStatusResponse.unavailableUnsafeDirectory).body,
            "an unavailable reason arriving as an error code reuses the unavailable copy"
        )
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: SessionGitError.git(code: "brand_new", message: "x")),
            "git refused the request (brand_new): x"
        )
    }

    func testErrorMessageForTerminalAndTransportStates() {
        XCTAssertEqual(SessionGitCopy.errorMessage(for: SessionGitError.network("x")), "Could not reach the agent. Check the connection and try again.")
        XCTAssertEqual(SessionGitCopy.errorMessage(for: SessionGitError.agentOutdated), "Update your agent to view changes.")
        XCTAssertEqual(SessionGitCopy.errorMessage(for: SessionGitError.sessionGone), "This session has ended.")
    }

    func testErrorMessageForUnmappedAPIErrorUsesDetailOrStatus() {
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: APIError(code: 409, errorBody: #"{"detail":"busy"}"#)),
            "busy"
        )
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: APIError(code: 500, errorBody: "oops")),
            "Request failed (HTTP 500)."
        )
    }

    func testUnavailableCopyHasATitlePerKnownReasonAndAGenericFallback() {
        XCTAssertEqual(SessionGitCopy.unavailable(GitStatusResponse.unavailableNotARepo).title, "Not a git repository")
        XCTAssertEqual(SessionGitCopy.unavailable(GitStatusResponse.unavailableBareRepository).title, "Bare repository")
        XCTAssertEqual(SessionGitCopy.unavailable(GitStatusResponse.unavailableCwdMissing).title, "Folder not found")
        XCTAssertEqual(SessionGitCopy.unavailable(GitStatusResponse.unavailableUnsafeDirectory).title, "Unsafe directory")
        XCTAssertEqual(SessionGitCopy.unavailable(GitStatusResponse.unavailableGitTooOld).title, "Git is too old")

        let future = SessionGitCopy.unavailable("shallow_clone")
        XCTAssertEqual(future.title, "Changes unavailable")
        XCTAssertEqual(future.body, "The agent could not read this repository (shallow_clone).")
    }

    // MARK: - Refusals

    func test409ObjectDetailMapsToGitWithTheRefusalCode() {
        XCTAssertEqual(
            mapped(APIError(code: 409, errorBody: #"{"detail":{"code":"index_locked","message":"index.lock exists"}}"#)),
            .git(code: SessionGitError.codeIndexLocked, message: "index.lock exists")
        )
        XCTAssertEqual(
            mapped(APIError(code: 400, errorBody: #"{"detail":{"code":"empty_message"}}"#)),
            .git(code: SessionGitError.codeEmptyMessage, message: nil)
        )
    }

    func testRefusalCopyPhrasesEach409Code() {
        XCTAssertEqual(SessionGitCopy.refusal(SessionGitError.codeIdentityRequired, message: nil), "Git doesn't know who you are on the agent machine.")
        XCTAssertEqual(SessionGitCopy.refusal(SessionGitError.codeUnmerged, message: nil), "Resolve the conflicts first.")
        XCTAssertEqual(SessionGitCopy.refusal(SessionGitError.codeNothingToCommit, message: nil), "Nothing is staged.")
        XCTAssertEqual(SessionGitCopy.refusal(SessionGitError.codeNoHead, message: nil), "There is no commit to amend yet.")
        XCTAssertEqual(SessionGitCopy.refusal(SessionGitError.codeHookFailed, message: "lint: nope"), "A commit hook rejected the commit.")
        XCTAssertEqual(SessionGitCopy.refusal(SessionGitError.codeIndexLocked, message: nil), "Another git process is running — try again.")
        XCTAssertEqual(SessionGitCopy.refusal(SessionGitError.codePathspecMismatch, message: nil), "That file changed under you — refreshed.")
        XCTAssertEqual(SessionGitCopy.refusal(SessionGitError.codeEmptyMessage, message: nil), "Enter a commit summary.")
    }

    func testRefusalCopyNamesAnUnknownCodeWithTheGitOutputWhenThereIsAny() {
        XCTAssertEqual(SessionGitCopy.refusal("rate_limited", message: nil), "git refused the request (rate_limited).")
        XCTAssertEqual(SessionGitCopy.refusal("rate_limited", message: "slow down"), "git refused the request (rate_limited): slow down")
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: SessionGitError.git(code: SessionGitError.codeIndexLocked, message: "x")),
            "Another git process is running — try again.",
            "the viewer copy reaches the refusal vocabulary for a 409"
        )
    }

    func testMutationMessageReadsGitTooOldAsTheMutationFloorNotThePollFloor() {
        XCTAssertEqual(
            SessionGitCopy.mutationMessage(for: SessionGitError.git(code: SessionGitError.codeGitTooOld, message: nil)),
            "Staging needs git 2.26 or newer on the agent machine."
        )
        XCTAssertEqual(
            SessionGitCopy.errorMessage(for: SessionGitError.git(code: SessionGitError.codeGitTooOld, message: nil)),
            "The agent host needs git 2.15 or newer."
        )
        XCTAssertEqual(
            SessionGitCopy.mutationMessage(for: SessionGitError.git(code: SessionGitError.codeIndexLocked, message: nil)),
            "Another git process is running — try again."
        )
        XCTAssertEqual(
            SessionGitCopy.mutationMessage(for: SessionGitError.network("x")),
            "Could not reach the agent. Check the connection and try again."
        )
    }

    func testIdentityFixCommandsAreVerbatim() {
        XCTAssertEqual(SessionGitCopy.identityFixCommands, [
            "git config --global user.name \"Your Name\"",
            "git config --global user.email \"you@example.com\"",
        ])
    }
}

/// The job routes' answers: a coded `detail` wins over the 404 branches, a
/// `job_running` refusal names the job to follow, and both have their copy.
final class SessionGitErrorsJobRoutesTests: XCTestCase {
    private func mapped(_ error: any Error) -> SessionGitError? {
        SessionGitError.map(error) as? SessionGitError
    }

    func testACoded404IsAGitErrorNeverAnOutdatedAgent() {
        // `GET …/git/jobs/{id}` answers `{"detail":{"code":"job_not_found"}}`;
        // read as `.agentOutdated` it would end the status feed and hide every
        // mutating control, so the object shape is classified first.
        XCTAssertEqual(
            mapped(APIError(code: 404, errorBody: #"{"detail":{"code":"job_not_found"}}"#)),
            .git(code: SessionGitError.codeJobNotFound, message: nil)
        )
        XCTAssertEqual(
            mapped(APIError(code: 404, errorBody: #"{"detail":{"code":"job_not_found","message":null}}"#)),
            .git(code: SessionGitError.codeJobNotFound, message: nil)
        )
    }

    func testTheString404sStillReadAsBefore() {
        XCTAssertEqual(mapped(APIError(code: 404, errorBody: #"{"detail":"Session 's1' not found"}"#)), .sessionGone)
        XCTAssertEqual(mapped(APIError(code: 404, errorBody: #"{"detail":"Not Found"}"#)), .agentOutdated)
    }

    func testJobRunning409CarriesTheActiveJobId() {
        XCTAssertEqual(
            mapped(APIError(code: 409, errorBody: #"{"detail":{"code":"job_running","message":"ab12cd34"}}"#)),
            .git(code: SessionGitError.codeJobRunning, message: "ab12cd34")
        )
    }

    func testRefusalCopyPhrasesTheTwoJobRefusals() {
        XCTAssertEqual(
            SessionGitCopy.refusal(SessionGitError.codeJobRunning, message: "ab12cd34"),
            "Another fetch, pull or push is still running."
        )
        XCTAssertEqual(
            SessionGitCopy.refusal(SessionGitError.codeJobNotFound, message: nil),
            "Lost track of the job — refresh to see the result."
        )
        XCTAssertEqual(
            SessionGitCopy.mutationMessage(for: SessionGitError.git(code: SessionGitError.codeJobRunning, message: "j9")),
            "Another fetch, pull or push is still running.",
            "the start path reaches the refusal vocabulary through the mutation copy"
        )
    }
}

/// The rebase routes' refusals and the copy each resolves to — the same
/// strings as Android and the web.
final class SessionGitErrorsRebaseTests: XCTestCase {
    private func git(_ code: String, _ message: String? = nil) -> SessionGitError {
        .git(code: code, message: message)
    }

    func testTheRebaseRefusalsMapWithTheRefStateOrPathTheyName() {
        XCTAssertEqual(
            SessionGitError.map(APIError(code: 409, errorBody: #"{"detail":{"code":"unknown_ref","message":"mian"}}"#)) as? SessionGitError,
            git(SessionGitError.codeUnknownRef, "mian")
        )
        XCTAssertEqual(
            SessionGitError.map(APIError(code: 409, errorBody: #"{"detail":{"code":"conflict_markers","message":"src/login.py"}}"#)) as? SessionGitError,
            git(SessionGitError.codeConflictMarkers, "src/login.py")
        )
        XCTAssertEqual(SessionGitError.codeNoRebase, "no_rebase")
    }

    func testRefusalCopyNamesTheFileThatStillHoldsConflictMarkers() {
        XCTAssertEqual(
            SessionGitCopy.refusal(SessionGitError.codeConflictMarkers, message: "src/login.py"),
            "src/login.py still has conflict markers — finish resolving it first."
        )
        XCTAssertEqual(
            SessionGitCopy.refusal(SessionGitError.codeConflictMarkers, message: nil),
            "The file still has conflict markers — finish resolving it first."
        )
    }

    func testRebaseMessageReadsTheSharedRefusalCodesTheWayARebaseMeansThem() {
        XCTAssertEqual(SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeUnknownRef, "nope")), "“nope” isn't a branch, tag or commit here.")
        XCTAssertEqual(SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeUnknownRef)), "That isn't a branch, tag or commit here.")
        XCTAssertEqual(SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeUnknownRef, "")), "That isn't a branch, tag or commit here.")
        XCTAssertEqual(SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeNoRebase, "clean")), "No rebase is in progress anymore.")
        XCTAssertEqual(
            SessionGitCopy.rebaseMessage(for: git(GitJobResponse.codeDirtyWorktree, "keep.txt")),
            "Local changes are in the way — stage, commit or discard them first."
        )
        XCTAssertEqual(
            SessionGitCopy.rebaseMessage(for: git(GitJobResponse.codeOperationInProgress, "merging")),
            "A rebase or merge is already in progress."
        )
        XCTAssertEqual(SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeNoHead)), "This branch has no commits yet.")
        XCTAssertEqual(SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeHookFailed, "nope")), "The pre-rebase hook refused the rebase.")
        XCTAssertEqual(
            SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeGitTooOld)),
            "Rebasing needs git 2.26 or newer on the agent machine."
        )
        XCTAssertEqual(
            SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeGitTimeout)),
            "The rebase took too long and was cut off — continue or abort it."
        )
    }

    func testRebaseMessageFallsBackToTheMutationCopyForEverythingElse() {
        XCTAssertEqual(
            SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeUnmerged, "unmerged paths in the index")),
            "Resolve the conflicts first."
        )
        XCTAssertEqual(SessionGitCopy.rebaseMessage(for: git(SessionGitError.codeJobRunning, "ab12")), "Another fetch, pull or push is still running.")
        XCTAssertEqual(
            SessionGitCopy.rebaseMessage(for: SessionGitError.network("x")),
            "Could not reach the agent. Check the connection and try again."
        )
    }

    func testRebaseOutputShowsGitsOutputOnlyForTheRefusalsItExplains() {
        XCTAssertEqual(
            SessionGitCopy.rebaseOutput(for: git(GitJobResponse.codeDirtyWorktree, "unstaged changes to tracked files")),
            "unstaged changes to tracked files"
        )
        XCTAssertEqual(
            SessionGitCopy.rebaseOutput(for: git(SessionGitError.codeHookFailed, "error: The pre-rebase hook refused to rebase.")),
            "error: The pre-rebase hook refused to rebase."
        )
        XCTAssertNil(SessionGitCopy.rebaseOutput(for: git(SessionGitError.codeHookFailed, "")))
        XCTAssertNil(SessionGitCopy.rebaseOutput(for: git(SessionGitError.codeHookFailed)))
        XCTAssertNil(SessionGitCopy.rebaseOutput(for: git(SessionGitError.codeUnknownRef, "nope")))
        XCTAssertNil(SessionGitCopy.rebaseOutput(for: SessionGitError.network("x")))
    }
}
