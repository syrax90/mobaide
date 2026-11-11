import XCTest
@testable import Mobaide

/// Which send paths arm the Static-mode typing latch (Android
/// `TerminalViewModelCursorRevealTest` parity).
///
/// The model-level sends — composer submit, control keys / chords, tapped row —
/// are all the user typing, so they arm. The terminal-surface funnel
/// (`sendTerminalBytes`, fed by the delegate's `send`) must not: it also
/// carries mouse clicks and wheel reports (a vertical drag in Claude Code is a
/// stream of wheel reports, and arming on it would yank the view sideways),
/// plus SwiftTerm's own auto-replies. Keystrokes arm in the terminal view
/// before SwiftTerm sends them. Each negative case also proves the bytes
/// reached the wire, so it cannot pass by the funnel simply dropping them.
@MainActor
final class TerminalSessionModelCursorRevealTests: XCTestCase {
    private var retainedCoordinator: TerminalHostView.Coordinator?

    private func poll(_ predicate: () -> Bool) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if predicate() { return true }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return predicate()
    }

    /// A live+running model wired to its Coordinator exactly as `makeUIView`
    /// does, so emitted bytes travel the real chain: terminal → delegate → PTY.
    private func wiredModel() async throws -> (TerminalSessionModel, FakeWebSocketConnection) {
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
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        let ready = await poll { model.connectionState == .live && model.isRunning }
        XCTAssertTrue(ready, "the harness must reach live+running before bytes can flow")
        XCTAssertFalse(model.terminalView.cursorReveal.isArmed, "precondition: nothing has armed the latch yet")
        return (model, connection)
    }

    private func inputFrames(_ connection: FakeWebSocketConnection) async -> [Data] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            let frames = connection.sentBinaries.filter { $0.first == 0x69 }
            if !frames.isEmpty { return frames }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return connection.sentBinaries.filter { $0.first == 0x69 }
    }

    // MARK: - Arming sends

    func testComposerSubmitArms() async throws {
        let (model, _) = try await wiredModel()

        model.sendInput("ls")

        XCTAssertTrue(model.terminalView.cursorReveal.isArmed)
    }

    func testControlKeyArms() async throws {
        let (model, _) = try await wiredModel()

        model.sendControlKey([0x7F])

        XCTAssertTrue(
            model.terminalView.cursorReveal.isArmed,
            "the input-bar row (Backspace, arrows, chords) is the user typing"
        )
    }

    // MARK: - Terminal-surface funnel

    func testWheelReportFromTheSurfaceDoesNotArm() async throws {
        let (model, connection) = try await wiredModel()

        // SGR wheel-up at col 10 row 5: what a vertical drag in a mouse-tracking TUI sends.
        model.sendTerminalBytes(Data("\u{1B}[<64;10;5M".utf8))

        let frames = await inputFrames(connection)
        XCTAssertEqual(frames.count, 1, "the report still reaches the PTY")
        XCTAssertFalse(
            model.terminalView.cursorReveal.isArmed,
            "a scrollback drag in Claude Code must not yank the Static view sideways"
        )
    }

    func testMouseClickThroughTheDelegateDoesNotArm() async throws {
        let (model, connection) = try await wiredModel()
        let coordinator = try XCTUnwrap(retainedCoordinator)

        coordinator.send(source: model.terminalView, data: Array("\u{1B}[<0;10;5M".utf8)[...])

        let frames = await inputFrames(connection)
        XCTAssertEqual(frames.count, 1)
        XCTAssertFalse(model.terminalView.cursorReveal.isArmed)
    }

    func testEmulatorAutoReplyDoesNotArm() async throws {
        let (model, connection) = try await wiredModel()

        connection.emit(.binary(Data([0x6F]) + Data("\u{1B}[6n".utf8)))

        let frames = await inputFrames(connection)
        XCTAssertEqual(frames.count, 1, "the cursor-position report still reaches the PTY")
        XCTAssertFalse(
            model.terminalView.cursorReveal.isArmed,
            "a program answering its own query is not the user typing"
        )
    }

    func testSurfaceBytesStillResumeFollowMode() async throws {
        let (model, _) = try await wiredModel()
        model.setFollowMode(false)

        model.sendTerminalBytes(Data("x".utf8))

        XCTAssertTrue(model.followMode, "splitting the funnel away from sendBytes must keep the follow-mode resume")
    }
}
