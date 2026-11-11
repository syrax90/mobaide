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

    // MARK: - isCancellation(_:)

    func testCancellationIsRecognisedForBothShapes() {
        XCTAssertTrue(SessionGitError.isCancellation(CancellationError()))
        XCTAssertTrue(SessionGitError.isCancellation(URLError(.cancelled)))
        XCTAssertFalse(SessionGitError.isCancellation(URLError(.timedOut)))
        XCTAssertFalse(SessionGitError.isCancellation(SessionGitError.sessionGone))
    }

    // MARK: - SessionGitCopy

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
}
