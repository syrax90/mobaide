import XCTest
@testable import Mobaide

/// The pure half of the conflict resolver: the marker scan (which must agree
/// with the agent's bytes-mode regex), the jump to the next conflict in UTF-16
/// units, the sides' names and roles, the whole-file picks and the copy shared
/// byte for byte with Android and the web.
final class GitConflictDisplayTests: XCTestCase {
    private func conflict(
        path: String = "src/login.py",
        code: String = "UU",
        state: String = GitRepoInfo.stateRebasing,
        kind: String = GitConflictResponse.kindText,
        content: String? = "a\n<<<<<<< HEAD\nmain\n=======\ntopic\n>>>>>>> fedcba9\n",
        ours: GitConflictSide = GitConflictSide(label: "origin/main", present: true),
        theirs: GitConflictSide = GitConflictSide(label: "fedcba9 Fix the login redirect", present: true)
    ) -> GitConflictResponse {
        GitConflictResponse(
            path: path, code: code, state: state, kind: kind, content: content, token: "t1",
            markers: 1, has_base: false, ours: ours, theirs: theirs, byte_limit: 262_144
        )
    }

    private let twoBlocks = "top\n<<<<<<< a\nx\n=======\ny\n>>>>>>> b\nmid\n<<<<<<< a\nz\n=======\n>>>>>>> b\n"

    private func offset(of needle: String, in text: String, from: Int = 0) -> Int {
        let ns = text as NSString
        return ns.range(of: needle, options: [], range: NSRange(location: from, length: ns.length - from)).location
    }

    // MARK: - Scan

    func testScanCountsOneConflictPerStartMarker() {
        XCTAssertEqual(GitConflictDisplay.scan(twoBlocks), ConflictScan(conflicts: 2, hasMarkers: true, hasBase: false))
    }

    func testScanSeesTheOriginalLinesOfADiff3Block() {
        XCTAssertEqual(
            GitConflictDisplay.scan("<<<<<<< ours\nm\n||||||| base\no\n=======\nt\n>>>>>>> theirs\n"),
            ConflictScan(conflicts: 1, hasMarkers: true, hasBase: true)
        )
    }

    func testScanFindsNothingInResolvedTextABareSeparatorIncluded() {
        XCTAssertEqual(GitConflictDisplay.scan("title\n=======\nbody\n"), ConflictScan())
        XCTAssertEqual(GitConflictDisplay.scan(""), ConflictScan())
    }

    func testScanReportsALeftoverEndMarkerAsNoOpenConflict() {
        XCTAssertEqual(
            GitConflictDisplay.scan("kept\n>>>>>>> fedcba9 (Fix)\n"),
            ConflictScan(conflicts: 0, hasMarkers: true, hasBase: false)
        )
    }

    func testScanFlagsATextOverTheByteLimitInUtf8BytesAndNeverWithoutOne() {
        // `é` is one character and two bytes; an emoji one character and four bytes.
        let accented = String(repeating: "é", count: 5)

        XCTAssertEqual(GitConflictDisplay.scan(accented, byteLimit: 10), ConflictScan())
        XCTAssertEqual(GitConflictDisplay.scan(accented + "a", byteLimit: 10), ConflictScan(tooLarge: true))
        XCTAssertEqual(GitConflictDisplay.scan("😀😀", byteLimit: 8), ConflictScan())
        XCTAssertEqual(GitConflictDisplay.scan("😀😀😀", byteLimit: 8), ConflictScan(tooLarge: true))
        XCTAssertEqual(GitConflictDisplay.scan(accented + "a"), ConflictScan())
        XCTAssertEqual(
            GitConflictDisplay.scan(twoBlocks, byteLimit: 16),
            ConflictScan(conflicts: 2, hasMarkers: true, hasBase: false, tooLarge: true)
        )
    }

    func testScanMatchesTheAgentsMarkerRule() {
        let cases: [(String, Bool)] = [
            ("<<<<<<<", true),
            ("<<<<<<<\r\nx", true),
            ("<<<<<<<\tlabel", true),
            ("<<<<<<<\u{0C}x", true),
            ("<<<<<<<\u{0B}x", true),
            ("<<<<<<<<< label", true),
            ("<<<<<< label", false),
            ("<<<<<<<label", false),
            (" <<<<<<< label", false),
            ("x <<<<<<< label", false),
            // whitespace to Foundation, not to Python's bytes-mode \s
            ("<<<<<<<\u{A0}label", false),
            ("<<<<<<<\u{2028}label", false),
        ]

        for (text, marker) in cases {
            let scan = GitConflictDisplay.scan(text)
            XCTAssertEqual(scan.hasMarkers, marker, text.debugDescription)
            XCTAssertEqual(scan.conflicts, marker ? 1 : 0, text.debugDescription)
        }
    }

    // MARK: - Next conflict

    func testNextConflictRangeFindsTheMarkerLineAfterTheCaretAndWraps() {
        let first = offset(of: "<<<<<<<", in: twoBlocks)
        let second = offset(of: "<<<<<<<", in: twoBlocks, from: first + 1)
        let length = "<<<<<<< a".utf16.count

        XCTAssertEqual(GitConflictDisplay.nextConflictRange(in: twoBlocks, after: 0), NSRange(location: first, length: length))
        XCTAssertEqual(GitConflictDisplay.nextConflictRange(in: twoBlocks, after: first + 3), NSRange(location: second, length: length))
        XCTAssertEqual(
            GitConflictDisplay.nextConflictRange(in: twoBlocks, after: first),
            NSRange(location: second, length: length),
            "a caret parked at the start of a marker line moves on"
        )
        XCTAssertEqual(GitConflictDisplay.nextConflictRange(in: twoBlocks, after: second), NSRange(location: first, length: length))
        XCTAssertEqual(
            GitConflictDisplay.nextConflictRange(in: twoBlocks, after: (twoBlocks as NSString).length),
            NSRange(location: first, length: length)
        )
    }

    func testNextConflictRangeCountsUtf16UnitsLikeTheTextViewsSelection() {
        let text = "😀 ж\n<<<<<<< a\n"

        let range = GitConflictDisplay.nextConflictRange(in: text, after: 0)

        XCTAssertEqual(range, NSRange(location: 5, length: 9))
        XCTAssertEqual(range.map { (text as NSString).substring(with: $0) }, "<<<<<<< a")
    }

    // MARK: - Kinds

    func testOnlyTextThatArrivedWithItsContentIsEditable() {
        XCTAssertTrue(GitConflictDisplay.isEditable(conflict()))
        XCTAssertTrue(GitConflictDisplay.isEditable(conflict(content: "")))
        XCTAssertFalse(GitConflictDisplay.isEditable(conflict(content: nil)))
        XCTAssertNil(GitConflictDisplay.kindNotice(conflict()))
    }

    func testWholeFilePicksExistForEveryKnownShapeButASubmoduleOrADirectoryInTheWay() {
        for kind in ["text", "too_large", "binary", "encoding", "missing", "symlink"] {
            XCTAssertTrue(GitConflictDisplay.canTakeSide(conflict(kind: kind)), kind)
        }
        for kind in ["submodule", "unsupported", "hologram"] {
            let shown = conflict(kind: kind, content: nil)
            XCTAssertFalse(GitConflictDisplay.canTakeSide(shown), kind)
            XCTAssertEqual(GitConflictDisplay.takeChoices(shown), [], kind)
        }
    }

    func testStartOverNeedsEditableTextBothSidesStillHave() {
        XCTAssertTrue(GitConflictDisplay.canStartOver(conflict()))
        XCTAssertFalse(GitConflictDisplay.canStartOver(conflict(code: "DU", ours: GitConflictSide(label: "origin/main", present: false))))
        XCTAssertFalse(GitConflictDisplay.canStartOver(conflict(code: "UD", theirs: GitConflictSide(label: "x", present: false))))
        XCTAssertFalse(GitConflictDisplay.canStartOver(conflict(kind: "binary", content: nil)))
    }

    // MARK: - Take choices

    func testTakeChoicesOfferEitherSideByItsLabelWhenBothHaveTheFile() {
        let body = "Every conflict in login.py is settled with this side and the file is marked resolved. Edits made to it are discarded."

        XCTAssertEqual(
            GitConflictDisplay.takeChoices(conflict()),
            [
                TakeChoice(
                    side: "ours", label: "Use origin/main for the whole file",
                    title: "Use origin/main for the whole file?", body: body, confirm: "Use this side", danger: false
                ),
                TakeChoice(
                    side: "theirs", label: "Use fedcba9 Fix the login redirect for the whole file",
                    title: "Use fedcba9 Fix the login redirect for the whole file?", body: body,
                    confirm: "Use this side", danger: false
                ),
            ]
        )
    }

    func testTakeChoicesTurnTheSideThatDeletedTheFileIntoTheDestructivePick() {
        let deletedByUs = conflict(path: "docs/old.md", code: "DU", ours: GitConflictSide(label: "origin/main", present: false))

        XCTAssertEqual(
            GitConflictDisplay.takeChoices(deletedByUs),
            [
                TakeChoice(
                    side: "ours", label: "Delete the file (origin/main)", title: "Delete old.md?",
                    body: "The file is removed, as on origin/main, and the conflict is marked resolved.",
                    confirm: "Delete file", danger: true
                ),
                TakeChoice(
                    side: "theirs", label: "Keep the file (fedcba9 Fix the login redirect)", title: "Keep old.md?",
                    body: "The file stays as fedcba9 Fix the login redirect has it and is marked resolved.",
                    confirm: "Keep file", danger: false
                ),
            ]
        )
    }

    func testTakeChoicesMirrorWhenTheOtherSideDeletedItAndLeaveOneWayOutWhenNeitherHasIt() {
        let deletedByThem = conflict(code: "UD", theirs: GitConflictSide(label: nil, present: false))
        let bothDeleted = conflict(
            code: "DD", kind: "missing", content: nil,
            ours: GitConflictSide(label: "origin/main", present: false),
            theirs: GitConflictSide(label: "x", present: false)
        )

        XCTAssertEqual(GitConflictDisplay.takeChoices(deletedByThem).map(\.label), ["Keep the file (origin/main)", "Delete the file (Incoming change)"])
        XCTAssertEqual(GitConflictDisplay.takeChoices(deletedByThem).map(\.danger), [false, true])
        XCTAssertEqual(GitConflictDisplay.takeChoices(bothDeleted).map(\.side), ["ours"])
        XCTAssertEqual(GitConflictDisplay.takeChoices(bothDeleted).map(\.label), ["Delete the file"])
        XCTAssertEqual(GitConflictDisplay.takeChoices(bothDeleted).map(\.danger), [true])
    }
}
