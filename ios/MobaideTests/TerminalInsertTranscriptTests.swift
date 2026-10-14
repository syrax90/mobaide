import XCTest
@testable import Mobaide

/// `TerminalSessionModel.insertTranscript`: a dictated transcript lands at the
/// composer's cursor (replacing a selection; at the end when the selection is
/// unknown, as on iOS 17), spaced from the words around it, with the cursor
/// after it and the composer revealed — and is never sent.
@MainActor
final class TerminalInsertTranscriptTests: XCTestCase {
    private func model(text: String, selection: Range<Int>?) -> TerminalSessionModel {
        let model = TerminalTestSupport.model()
        model.inputText = text
        model.composerSelection = selection
        return model
    }

    func testIntoAnEmptyComposerTheTranscriptIsTheTextWithTheCursorAfterIt() {
        let model = model(text: "", selection: nil)

        model.insertTranscript("  fix the login bug \n")

        XCTAssertEqual(model.inputText, "fix the login bug")
        XCTAssertEqual(model.composerSelection, 17..<17)
    }

    func testRightAfterAWordLeadsWithASpaceAndReusesTheSpaceThatFollows() {
        let model = model(text: "please now", selection: 6..<6)

        model.insertTranscript("fix it")

        XCTAssertEqual(model.inputText, "please fix it now")
        XCTAssertEqual(model.composerSelection, 13..<13)
    }

    func testInsideAWordSpacesItFromBothHalves() {
        let model = model(text: "pleasenow", selection: 6..<6)

        model.insertTranscript("fix it")

        XCTAssertEqual(model.inputText, "please fix it now")
        XCTAssertEqual(model.composerSelection, 14..<14)
    }

    func testASelectionIsReplaced() {
        let model = model(text: "run the old tests", selection: 8..<12)

        model.insertTranscript("new")

        XCTAssertEqual(model.inputText, "run the new tests")
        XCTAssertEqual(model.composerSelection, 12..<12)
    }

    func testAnUnknownSelectionAppendsAtTheEnd() {
        let model = model(text: "git", selection: nil)

        model.insertTranscript("status")

        XCTAssertEqual(model.inputText, "git status")
        XCTAssertEqual(model.composerSelection, 10..<10)
    }

    func testAStaleSelectionPastTheEndIsClamped() {
        let model = model(text: "git", selection: 9..<12)

        model.insertTranscript("status")

        XCTAssertEqual(model.inputText, "git status")
    }

    func testCountsCharactersNotUTF16Units() {
        let model = model(text: "👍 done", selection: 1..<1)

        model.insertTranscript("all")

        XCTAssertEqual(model.inputText, "👍 all done")
    }

    func testABlankTranscriptChangesNothing() {
        let model = model(text: "keep me", selection: 4..<4)
        model.hideComposer()
        let request = model.composerCursorRequest

        model.insertTranscript("   ")

        XCTAssertEqual(model.inputText, "keep me")
        XCTAssertEqual(model.composerSelection, 4..<4)
        XCTAssertEqual(model.composerCursorRequest, request)
        XCTAssertFalse(model.composerVisible)
    }

    func testRevealsAHiddenComposerRequestsFocusAndMovesTheCursor() async throws {
        let model = model(text: "", selection: nil)
        model.hideComposer()
        let request = model.composerCursorRequest
        let focusEvents = StreamCollector(model.focusRequests())

        model.insertTranscript("rm -rf build")

        XCTAssertTrue(model.composerVisible)
        XCTAssertEqual(model.composerCursorRequest, request + 1)
        let focused = try await focusEvents.next()
        XCTAssertTrue(focused)
        XCTAssertEqual(model.inputText, "rm -rf build", "the transcript is staged for review, never sent")
    }
}
