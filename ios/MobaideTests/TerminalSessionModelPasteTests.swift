import Foundation
import XCTest
@testable import Mobaide

/// Port of Android `TerminalViewModelPasteTest`: pasted bytes must reach the PTY
/// as `i` (input) frames and inherit the Live+running gate, with no
/// paste-specific bypass.
@MainActor
final class TerminalSessionModelPasteTests: XCTestCase {
    private var retainedCoordinator: TerminalHostView.Coordinator?

    private func wiredModel(running: Bool = true) async throws -> (TerminalSessionModel, FakeWebSocketConnection) {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(transportFactory: factory)
        let coordinator = TerminalHostView.Coordinator(model: model)
        retainedCoordinator = coordinator
        model.terminalView.terminalDelegate = coordinator
        model.terminalView.frame = CGRect(x: 0, y: 0, width: 390, height: 600)
        model.terminalView.layoutIfNeeded()
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        guard running else { return (model, connection) }
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        let ready = await poll { model.connectionState == .live && model.isRunning }
        XCTAssertTrue(ready, "the harness must reach live+running before bytes can flow")
        return (model, connection)
    }

    private func poll(_ condition: () -> Bool) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return condition()
    }

    /// Frames reach `sentBinaries` asynchronously (enqueue → AsyncStream →
    /// sendTask), so a count read straight after the call is stale.
    private func drainedFrames(
        _ connection: FakeWebSocketConnection,
        expected: Int
    ) async -> [Data] {
        _ = await poll { connection.sentBinaries.filter { $0.first == 0x69 }.count >= expected }
        try? await Task.sleep(for: .milliseconds(50))
        return connection.sentBinaries.filter { $0.first == 0x69 }
    }

    private func payloads(_ frames: [Data]) -> [String] {
        frames.map { String(decoding: $0.dropFirst(), as: UTF8.self) }
    }

    func testPasteSendsInputFramesNeverASubmitFrame() async throws {
        let (model, connection) = try await wiredModel()
        model.clipboardReader = { "echo hi" }

        model.pasteFromClipboard()

        let frames = await drainedFrames(connection, expected: 1)
        XCTAssertEqual(payloads(frames), ["echo hi"])
        XCTAssertTrue(
            connection.sentBinaries.allSatisfy { $0.first != 0x6D },
            "paste must never route through sendInput's 'm' submit frame — that encoder wraps multi-line bodies unconditionally and would double-wrap the markers"
        )
    }

    /// The markers and body travel in **one** frame, unlike Android's three.
    /// Android writes to a local PTY fd where the three are effectively atomic;
    /// these cross a WebSocket and a relay whose send result the drain loop
    /// discards, so losing the closing marker alone would strand the program's
    /// line editor in paste-collect mode — every later keystroke swallowed as
    /// paste content, which reads as a dead keyboard. The PTY sees identical
    /// bytes; only the framing differs.
    func testMultilineBracketedPasteTravelsAsOneAtomicFrame() async throws {
        let (model, connection) = try await wiredModel()
        // DECSET 2004, the way a real CLI turns bracketed paste on.
        model.terminalView.feed(text: "\u{1B}[?2004h")
        XCTAssertTrue(model.terminalView.getTerminal().bracketedPasteMode)
        model.clipboardReader = { "a\nb" }

        model.pasteFromClipboard()

        let frames = await drainedFrames(connection, expected: 1)
        XCTAssertEqual(frames.count, 1, "a partial bracketed paste must not be representable on the wire")
        XCTAssertEqual(payloads(frames), ["\u{1B}[200~a\rb\u{1B}[201~"])
    }

    func testPasteIsDroppedWhenTheSessionIsNotRunning() async throws {
        let (model, connection) = try await wiredModel(running: false)
        model.clipboardReader = { "echo hi" }

        model.pasteFromClipboard()

        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            connection.sentBinaries.filter { $0.first == 0x69 }.isEmpty,
            "paste inherits the same Live+running gate as every other terminal-originated write"
        )
    }

    func testPasteDismissesAnActiveSelectionFirst() async throws {
        let (model, _) = try await wiredModel()
        model.terminalView.feed(text: "hello world\r\n")
        let cell = model.terminalView.cellSize
        model.terminalView.selectionController.beginSelection(
            at: CGPoint(x: cell.width / 2, y: cell.height / 2)
        )
        XCTAssertTrue(model.terminalView.isSelectionActive)
        model.clipboardReader = { "echo hi" }

        model.pasteFromClipboard()

        XCTAssertFalse(
            model.terminalView.isSelectionActive,
            "Android calls stopTextSelectionMode() before its paste sink runs"
        )
    }

    // MARK: - Selection actions

    func testCopySelectionWritesToTheClipboardAndClearsTheSelection() async throws {
        let (model, _) = try await wiredModel()
        model.terminalView.feed(text: "cd /usr/local/bin\r\n")
        let cell = model.terminalView.cellSize
        model.terminalView.selectionController.beginSelection(
            at: CGPoint(x: cell.width * 5.5, y: cell.height / 2)
        )

        model.copySelection()

        XCTAssertEqual(UIPasteboard.general.string, "/usr/local/bin")
        XCTAssertFalse(model.terminalView.isSelectionActive)
    }

    func testSendSelectionToInputPopulatesTheComposerWithoutSubmitting() async throws {
        let (model, connection) = try await wiredModel()
        model.terminalView.feed(text: "cd /usr/local/bin\r\n")
        let cell = model.terminalView.cellSize
        model.terminalView.selectionController.beginSelection(
            at: CGPoint(x: cell.width * 5.5, y: cell.height / 2)
        )

        model.sendSelectionToInput()

        XCTAssertEqual(model.inputText, "/usr/local/bin")
        XCTAssertTrue(model.composerVisible)
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            connection.sentBinaries.allSatisfy { $0.first != 0x6D && $0.first != 0x78 },
            "Send to Input fills the field only — it must never submit or execute"
        )
        XCTAssertFalse(model.terminalView.isSelectionActive)
    }
}
