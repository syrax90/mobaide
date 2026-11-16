import XCTest
@testable import Mobaide

/// The `pane` block of a Herdr pane session's state frame. A grid read where
/// there is none would stop the resize frames of a normal session; a grid
/// missed in watch mode would let this client resize a desktop pane.
final class TerminalPaneStateTests: XCTestCase {
    private func parse(_ pane: String) -> TerminalPaneState? {
        TerminalPaneState.parse(#"{"status":"running","pane":\#(pane)}"#)
    }

    func testWatchBlockDictatesItsGrid() {
        let state = parse(#"{"mode":"watch","cols":84,"rows":47}"#)

        XCTAssertEqual(state, TerminalPaneState(mode: "watch", cols: 84, rows: 47))
        XCTAssertEqual(state?.grid, TerminalGrid(cols: 84, rows: 47))
    }

    func testControlBlockOfAnotherClientDictatesThatClientsGrid() {
        // A size from a watcher would resize the pane under the client that controls it.
        let state = parse(#"{"mode":"control","controller":"another-device","cols":40,"rows":20}"#)

        XCTAssertEqual(state?.grid, TerminalGrid(cols: 40, rows: 20))
        XCTAssertEqual(state?.isControlledElsewhere, true)
        XCTAssertEqual(state?.isControl, false)
    }

    func testControlBlockOfThisClientLeavesTheSizeToIt() {
        // A dictated grid here would stop the resize frames that control exists for.
        let state = parse(#"{"mode":"control","controller":"\#(PaneClient.id)","cols":40,"rows":20}"#)

        XCTAssertNotNil(state)
        XCTAssertNil(state?.grid)
        XCTAssertEqual(state?.isControl, true)
        XCTAssertEqual(state?.isControlledElsewhere, false)
    }

    func testStateOfANormalSessionHasNoPaneBlock() {
        XCTAssertNil(TerminalPaneState.parse(#"{"status":"running","pid":1234}"#))
        XCTAssertNil(parse("null"))
        XCTAssertNil(TerminalPaneState.parse("not json"))
    }

    func testWatchBlockItCannotDrawHasNoGrid() {
        for pane in [
            #"{"mode":"watch"}"#,
            #"{"mode":"watch","cols":84}"#,
            #"{"mode":"watch","cols":84,"rows":null}"#,
            #"{"mode":"watch","cols":0,"rows":47}"#,
            #"{"mode":"watch","cols":84,"rows":-1}"#,
            #"{"mode":"watch","cols":"84","rows":"47"}"#,
            #"{"mode":"observe","cols":84,"rows":47}"#,
        ] {
            XCTAssertNil(parse(pane)?.grid, pane)
        }
        XCTAssertNil(parse(#"{"cols":84,"rows":47}"#))
    }
}
