import XCTest
@testable import Mobaide

/// Covers `composerVisible` (mirrors Android
/// `TerminalViewModelComposerVisibilityTest`): purely user-controlled — hide
/// has no focus side effect and nothing in the connection machinery may
/// resurrect a hidden composer; reveal paths (explicit, `setInput`,
/// `applyTemplate`) show it and request field focus.
@MainActor
final class TerminalComposerVisibilityTests: XCTestCase {
    private func connectedModel() async throws -> (TerminalSessionModel, FakeWebSocketConnection) {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(transportFactory: factory)
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        return (model, connection)
    }

    func testComposerDefaultsVisible() {
        XCTAssertTrue(TerminalTestSupport.model().composerVisible)
    }

    func testHideComposerHidesWithoutFocusSideEffect() async {
        let model = TerminalTestSupport.model()
        let focusEvents = StreamCollector(model.focusRequests())

        model.hideComposer()

        XCTAssertFalse(model.composerVisible)
        let silent = await focusEvents.expectNoElement()
        XCTAssertTrue(silent, "hiding must not emit a focus request (Android parity)")
    }

    func testRevealComposerShowsAndEmitsFocus() async throws {
        let model = TerminalTestSupport.model()
        model.hideComposer()
        let focusEvents = StreamCollector(model.focusRequests())

        model.revealComposer()

        XCTAssertTrue(model.composerVisible)
        _ = try await focusEvents.next()
    }

    func testHiddenComposerSurvivesReconnect() async throws {
        let (model, connection) = try await connectedModel()
        connection.emit(.binary(Data([0x62]))) // replayStart
        connection.emit(.binary(Data([0x65]))) // replayEnd → live
        await waitUntil { model.connectionState == .live }

        model.hideComposer()

        connection.emit(.closed(code: 1006, reason: "drop"))
        await waitUntil { model.connectionState == .reconnecting }
        connection.emit(.opened)
        connection.emit(.binary(Data([0x62])))
        connection.emit(.binary(Data([0x65])))
        await waitUntil { model.connectionState == .live }

        XCTAssertFalse(
            model.composerVisible,
            "hidden is user state — a reconnect/replay cycle must not resurrect the composer"
        )
    }

    func testSetInputWithFocusRevealsComposer() async throws {
        let model = TerminalTestSupport.model()
        model.hideComposer()
        let focusEvents = StreamCollector(model.focusRequests())

        model.setInput("git status", requestFocus: true)

        XCTAssertTrue(model.composerVisible, "double-tap copy must auto-reveal the hidden composer")
        XCTAssertEqual(model.inputText, "git status")
        _ = try await focusEvents.next()
    }

    func testApplyTemplateRevealsComposer() async throws {
        let model = TerminalTestSupport.model()
        model.hideComposer()
        let focusEvents = StreamCollector(model.focusRequests())

        model.applyTemplate(makeTemplate(id: "t1", name: "n", body: "run tests"))

        XCTAssertTrue(model.composerVisible)
        XCTAssertEqual(model.inputText, "run tests")
        _ = try await focusEvents.next()
    }
}
