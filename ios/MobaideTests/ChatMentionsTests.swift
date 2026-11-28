import XCTest
@testable import Mobaide

/// `ChatMentions`: which `@` token the cursor is in, what a picked suggestion
/// turns it into, and which picked files a sent prompt carries. Same rules as
/// web `chatMentions.ts` and Android `ChatMentions.kt`.
final class ChatMentionsTests: XCTestCase {

    func testTokenFindsTheAtTokenAroundTheCursor() {
        XCTAssertEqual(ChatMentions.token(in: "@sr", cursor: 3), .init(start: 0, end: 3, query: "sr"))
        XCTAssertEqual(ChatMentions.token(in: "see @src/ap now", cursor: 11), .init(start: 4, end: 11, query: "src/ap"))
        // Back inside a picked mention: the query is what lies before the cursor.
        XCTAssertEqual(ChatMentions.token(in: "@src/app.py", cursor: 4), .init(start: 0, end: 11, query: "src"))
        // Offsets count Characters, as the composer's selection does.
        XCTAssertEqual(ChatMentions.token(in: "👍🏽 @sr", cursor: 5), .init(start: 2, end: 5, query: "sr"))
    }

    func testTokenFindsNoneInAnEmailAnotherWordOrPlainText() {
        XCTAssertNil(ChatMentions.token(in: "mail me@host.com", cursor: 16))
        XCTAssertNil(ChatMentions.token(in: "a @b c", cursor: 6))
        XCTAssertNil(ChatMentions.token(in: "plain", cursor: 3))
        XCTAssertNil(ChatMentions.token(in: "", cursor: 0))
    }

    func testTokenFindsNoneWithTheCursorJustInFrontOfTheAt() {
        XCTAssertNil(ChatMentions.token(in: "@sr", cursor: 0))
        // composerText.didSet reads the cursor from before the typed "@".
        XCTAssertNil(ChatMentions.token(in: "hi @", cursor: 3))
    }

    func testTokenKeepsAPickedPathWithSpacesAsOneToken() throws {
        let inFolder = "see @my docs/re"
        let backInFile = "see @my docs/read me.md now"

        let fileToken = try XCTUnwrap(ChatMentions.token(in: backInFile, cursor: 7, picked: ["my docs/read me.md"]))
        let replaced = ChatMentions.insert("my docs/other.md", replacing: fileToken, in: backInFile)

        XCTAssertNil(ChatMentions.token(in: inFolder, cursor: 15))
        XCTAssertEqual(
            ChatMentions.token(in: inFolder, cursor: 15, picked: ["my docs/"]),
            .init(start: 4, end: 15, query: "my docs/re")
        )
        XCTAssertEqual(fileToken, .init(start: 4, end: 23, query: "my"))
        XCTAssertEqual(replaced.text, "see @my docs/other.md now")
        XCTAssertEqual(replaced.cursor, 22)
    }

    func testInsertEndsAFileWithOneSpaceAndLeavesTheCursorInsideAFolder() {
        let atEnd = ChatMentions.insert("src/app.py", replacing: .init(start: 0, end: 3, query: "sr"), in: "@sr")
        let midText = ChatMentions.insert("src/app.py", replacing: .init(start: 4, end: 7, query: "sr"), in: "fix @sr now")
        let folder = ChatMentions.insert("src/", replacing: .init(start: 0, end: 3, query: "sr"), in: "@sr")

        XCTAssertEqual(atEnd.text, "@src/app.py ")
        XCTAssertEqual(atEnd.cursor, 12)
        XCTAssertEqual(midText.text, "fix @src/app.py now")
        XCTAssertEqual(midText.cursor, 16)
        XCTAssertEqual(folder.text, "@src/")
        XCTAssertEqual(folder.cursor, 5)
        XCTAssertEqual(ChatMentions.token(in: folder.text, cursor: folder.cursor)?.query, "src/")
    }

    func testInTextSendsThePickedMentionsTheTextStillNamesInPickOrderOnceEach() {
        let picked = ["src/app.py", "gone.ts", "docs/", "src/app.py", "lib/x.ts", "lib/y.ts"]
        let text = "Explain @src/app.py, then @docs/ (see lib/x.ts) x@lib/y.ts @src/app.py.bak"

        XCTAssertEqual(ChatMentions.inText(text, picked: picked), ["src/app.py", "docs/"])
    }

    func testInTextSendsOnlyTheLongestPickedPathATokenNames() {
        let picked = ["notes.txt", "notes.txt backup"]

        XCTAssertEqual(ChatMentions.inText("Read @notes.txt backup ", picked: picked), ["notes.txt backup"])
        XCTAssertEqual(ChatMentions.inText("Read @notes.txt and @notes.txt backup", picked: picked), picked)
    }

    func testInTextSendsAtMostMaxMentions() {
        let picked = (0..<(ChatMentions.maxMentions + 2)).map { "f\($0).ts" }
        let text = picked.map { "@" + $0 }.joined(separator: " ")

        XCTAssertEqual(ChatMentions.inText(text, picked: picked), Array(picked.prefix(ChatMentions.maxMentions)))
    }
}
