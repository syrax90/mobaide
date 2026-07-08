import SwiftUI
import XCTest
@testable import Mobaide

final class AnsiParserTests: XCTestCase {
    private struct FixtureFile: Decodable {
        let cases: [Case]
    }

    private struct Case: Decodable {
        let name: String
        let input: String
        let runs: [Run]
    }

    private struct Run: Decodable {
        let text: String
        let fg: [Int]?
        let bg: [Int]?
        let bold: Bool?
        let italic: Bool?
        let underline: Bool?

        var expected: AnsiRun {
            AnsiRun(
                text: text,
                foreground: fg.map { AnsiRGB(r: $0[0], g: $0[1], b: $0[2]) },
                background: bg.map { AnsiRGB(r: $0[0], g: $0[1], b: $0[2]) },
                bold: bold ?? false,
                italic: italic ?? false,
                underline: underline ?? false
            )
        }
    }

    func testGoldenRunsFromFixtures() throws {
        let file = try FixtureLoader.load(FixtureFile.self, path: "ansi/ansi_sgr.json")
        XCTAssertGreaterThanOrEqual(file.cases.count, 20)
        for testCase in file.cases {
            let actual = AnsiParser.parseRuns(testCase.input)
            let expected = testCase.runs.map(\.expected)
            XCTAssertEqual(actual, expected, "case \(testCase.name)")
        }
    }

    func testParseStripsEscapesAndPreservesText() {
        let attributed = AnsiParser.parse("\u{1B}[31mred\u{1B}[0m green \u{1B}[38;5;196mX")
        XCTAssertEqual(String(attributed.characters), "red green X")
    }

    func testParseEmptyInputIsEmpty() {
        XCTAssertEqual(String(AnsiParser.parse("").characters), "")
    }

    func testParseAppliesForegroundColor() {
        let attributed = AnsiParser.parse("\u{1B}[31mred")
        let runs = Array(attributed.runs)
        XCTAssertEqual(runs.count, 1)
        XCTAssertEqual(runs.first?.foregroundColor, Color(ansi: AnsiRGB(hex: 0xCD3131)))
    }

    func testCube256IndexFormula() {
        // index = 16 + 36r + 6g + b; xterm levels {0,95,135,175,215,255}.
        let runs = AnsiParser.parseRuns("\u{1B}[38;5;196mX")
        XCTAssertEqual(runs, [AnsiRun(text: "X", foreground: AnsiRGB(r: 255, g: 0, b: 0))])
    }
}
