import Foundation
import XCTest
@testable import Mobaide

/// The submit rule and the gates shared by the inline composer (`InputBar`) and
/// the expanded editor (`FullScreenInputSheet`). Both surfaces call
/// `submitComposer` / read `canSubmitComposer`, so these assertions cover the
/// two of them at once.
@MainActor
final class TerminalComposerSubmitTests: XCTestCase {
    private func liveModel(
        running: Bool = true
    ) async throws -> (TerminalSessionModel, FakeWebSocketConnection) {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(transportFactory: factory)
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        guard running else { return (model, connection) }
        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"running"}"#.utf8)))
        let ready = await poll { model.connectionState == .live && model.isRunning }
        XCTAssertTrue(ready, "the harness must reach live+running before the composer opens")
        return (model, connection)
    }

    private func poll(_ condition: () -> Bool) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(5))
        }
        return condition()
    }

    /// Frames reach `sentBinaries` asynchronously (enqueue → AsyncStream →
    /// sendTask), so a read straight after the call is stale.
    private func submitFrames(_ connection: FakeWebSocketConnection, atLeast count: Int) async -> [Data] {
        _ = await poll { connection.sentBinaries.filter { $0.first == 0x6D }.count >= count }
        return connection.sentBinaries.filter { $0.first == 0x6D }
    }

    // MARK: - submitComposer

    func testSubmitComposerTrimsTrailingNewlinesAndClearsBuffer() async throws {
        let (model, connection) = try await liveModel()
        model.inputText = "hello\r\n\n"

        model.submitComposer()

        let sent = await submitFrames(connection, atLeast: 1)
        XCTAssertEqual(sent.first, Data([0x6D, 0x01, 0x0B]) + Data("hello".utf8))
        XCTAssertEqual(model.inputText, "", "a sent buffer is cleared for the next prompt")
    }

    /// The expanded editor's whole purpose is that Return inserts newlines, so a
    /// buffer of nothing but newlines is reachable by holding Return. It must
    /// not reach the PTY as an empty submit.
    func testSubmitComposerIsNoOpForNewlineOnlyBufferAndKeepsTheDraft() async throws {
        let (model, connection) = try await liveModel()
        model.inputText = "\n\n\r"

        model.submitComposer()

        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(
            connection.sentBinaries.filter { $0.first == 0x6D }.isEmpty,
            "a buffer that is empty once trimmed must send no submit frame"
        )
        XCTAssertEqual(model.inputText, "\n\n\r", "a refused submit must not wipe the draft")
    }

    func testSubmitComposerWrapsMultilinePromptInBracketedPaste() async throws {
        let (model, connection) = try await liveModel()
        model.inputText = "first line\nsecond line\n"

        model.submitComposer()

        let sent = await submitFrames(connection, atLeast: 1)
        var expected = Data([0x6D, 0x01, 0x0B])
        expected += Data("\u{1B}[200~first line\nsecond line\u{1B}[201~".utf8)
        XCTAssertEqual(
            sent.first,
            expected,
            "only the trailing newline is trimmed — the interior ones are what bracketed paste protects"
        )
    }

    /// Only the trailing run is trimmed. A blank opening line is the user's own
    /// text and reaches the PTY inside the bracketed paste untouched — unlike
    /// `canSubmitComposer`, whose trim is two-sided and only ever decides
    /// whether the button is offered.
    func testSubmitComposerKeepsLeadingNewlines() async throws {
        let (model, connection) = try await liveModel()
        model.inputText = "\n\nls\n"

        model.submitComposer()

        let sent = await submitFrames(connection, atLeast: 1)
        var expected = Data([0x6D, 0x01, 0x0B])
        expected += Data("\u{1B}[200~\n\nls\u{1B}[201~".utf8)
        XCTAssertEqual(sent.first, expected, "the trim is one-sided — a leading blank line is content")
    }

    /// Swift folds `CR LF` into one grapheme cluster equal to neither `"\n"`
    /// nor `"\r"`, so a `Character`-level scan calls this single-line, skips
    /// the bracketed-paste wrap, and lets the bare CR submit the prompt at
    /// the first line break.
    func testSubmitComposerWrapsCrlfPromptInBracketedPaste() async throws {
        let (model, connection) = try await liveModel()
        model.inputText = "first line\r\nsecond line\r\n"

        model.submitComposer()

        let sent = await submitFrames(connection, atLeast: 1)
        var expected = Data([0x6D, 0x01, 0x0B])
        expected += Data("\u{1B}[200~first line\r\nsecond line\u{1B}[201~".utf8)
        XCTAssertEqual(sent.first, expected)
    }

    /// The inline composer stays on screen after Send, leaving its button under
    /// the user's thumb, so the submit rule has to disarm itself: the send clears
    /// the buffer, which withdraws the gate and makes the next tap a no-op
    /// instead of a duplicate prompt.
    func testSendDisarmsItselfSoASecondTapCannotResendThePrompt() async throws {
        let (model, connection) = try await liveModel()
        model.inputText = "deploy to prod"

        model.submitComposer()
        let sent = await submitFrames(connection, atLeast: 1)
        XCTAssertEqual(sent.count, 1)
        XCTAssertFalse(model.canSubmitComposer, "an emptied buffer withdraws the Send gate")

        model.submitComposer()
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(
            connection.sentBinaries.filter { $0.first == 0x6D }.count, 1,
            "a second tap on a cleared buffer must not repeat the prompt"
        )
    }

    // MARK: - canSubmitComposer

    func testCanSubmitComposerFalseForWhitespaceOnlyBuffer() async throws {
        let (model, _) = try await liveModel()
        model.inputText = "  \n \t"
        XCTAssertFalse(
            model.canSubmitComposer,
            "Send must not offer itself for a buffer submitComposer would refuse"
        )

        model.inputText = " ls "
        XCTAssertTrue(model.canSubmitComposer)
    }

    func testCanSubmitComposerWithdrawnWhenSessionStopsWhileSocketStaysLive() async throws {
        let (model, connection) = try await liveModel()
        model.inputText = "ls"
        XCTAssertTrue(model.canSubmitComposer)

        connection.emit(.binary(Data([0x73]) + Data(#"{"status":"completed"}"#.utf8)))
        let stopped = await poll { !model.isRunning }
        XCTAssertTrue(stopped)

        XCTAssertEqual(model.connectionState, .live, "only the session stopped — the socket stays live")
        XCTAssertFalse(model.canSubmitComposer)
    }

    // MARK: - isComposerActive

    /// Android gates the composer and the expand affordance on `isConnected &&
    /// running`, which spans the replay window — deliberately wider than
    /// `canSendTerminalBytes`, whose keys would be dropped during replay.
    func testIsComposerActiveStaysTrueDuringReplayUnlikeTerminalByteGate() async throws {
        let (model, connection) = try await liveModel()
        connection.emit(.binary(Data([0x62]))) // replayStart
        let replaying = await poll { model.connectionState == .replayingBuffer && model.isRunning }
        XCTAssertTrue(replaying)

        XCTAssertTrue(model.isComposerActive)
        XCTAssertFalse(model.canSendTerminalBytes)
    }

    /// A network drop mid-draft is not a stopped session: the socket leaves
    /// `.live` while the PTY keeps running. The gate's `isConnected` half is the
    /// only thing that catches it, and the editor freezing must not cost the
    /// user the paragraph they were writing.
    func testComposerClosesWhenTheSocketDropsWhileTheSessionKeepsRunning() async throws {
        let (model, connection) = try await liveModel()
        model.inputText = "half a prompt"
        XCTAssertTrue(model.canSubmitComposer)

        connection.emit(.closed(code: 1006, reason: "drop"))
        let dropped = await poll { model.connectionState == .reconnecting }
        XCTAssertTrue(dropped)

        XCTAssertTrue(model.isRunning, "the PTY never stopped — only the socket went away")
        XCTAssertFalse(model.isComposerActive)
        XCTAssertFalse(model.canSubmitComposer)
        XCTAssertEqual(model.inputText, "half a prompt", "a frozen editor keeps the draft it was holding")
    }
}
