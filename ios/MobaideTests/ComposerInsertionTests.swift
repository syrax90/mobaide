import XCTest
@testable import Mobaide

/// `ComposerInsertion.insert` (Android `insertAtCursor`) and the terminal
/// composer's template path: a template body goes in at the cursor as is,
/// replacing a selection, with the rest of the draft kept and the cursor
/// right after it. The spaced (transcript) path is covered by
/// `TerminalInsertTranscriptTests`.
@MainActor
final class ComposerInsertionTests: XCTestCase {
    func testAtTheCursorTheTextGoesInAsIsAndTheDraftIsKept() {
        let result = ComposerInsertion.insert("\n- step\n", into: "pleasenow", selection: 6..<6, spaced: false)

        XCTAssertEqual(result.text, "please\n- step\nnow")
        XCTAssertEqual(result.cursor, 14)
    }

    func testASelectionIsReplaced() {
        let result = ComposerInsertion.insert("new", into: "run the old tests", selection: 8..<11, spaced: false)

        XCTAssertEqual(result.text, "run the new tests")
        XCTAssertEqual(result.cursor, 11)
    }

    func testAnUnknownSelectionAppendsAtTheEnd() {
        let result = ComposerInsertion.insert(" status", into: "git", selection: nil, spaced: false)

        XCTAssertEqual(result.text, "git status")
        XCTAssertEqual(result.cursor, 10)
    }

    func testSpacedAddsASpaceOnlyWhereTheTextWouldRunIntoAWord() {
        let inside = ComposerInsertion.insert("fix it", into: "pleasenow", selection: 6..<6, spaced: true)
        let afterASpace = ComposerInsertion.insert("fix it", into: "please now", selection: 7..<7, spaced: true)

        XCTAssertEqual(inside.text, "please fix it now")
        XCTAssertEqual(afterASpace.text, "please fix it now")
        XCTAssertEqual(afterASpace.cursor, 14)
    }

    func testTheTerminalTemplateGoesInAtTheCursorAndKeepsTheDraft() {
        let model = TerminalTestSupport.model()
        model.inputText = "run  now"
        model.composerSelection = 4..<4

        model.applyTemplate(makeTemplate(id: "t1", name: "tests", body: "the tests"))

        XCTAssertEqual(model.inputText, "run the tests now")
        XCTAssertEqual(model.composerSelection, 13..<13)
    }
}
