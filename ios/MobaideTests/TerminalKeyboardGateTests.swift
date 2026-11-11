import UIKit
import XCTest
@testable import Mobaide

/// Covers the explicit-only terminal keyboard: `MobaideTerminalView`'s
/// responder gate (nothing may raise the keyboard except the InputBar button
/// opening `keyboardGate` first) and the `TerminalSessionModel` show/hide
/// plumbing built on it. Responder transitions need a key window, which the
/// hosted test bundle (TEST_HOST = Mobaide.app) provides.
@MainActor
final class TerminalKeyboardGateTests: XCTestCase {
    private func makeTerminal() -> MobaideTerminalView {
        let terminal = MobaideTerminalView(frame: .zero)
        terminal.configureAppearance()
        return terminal
    }

    /// Mounts `view` in a key window and tears both down even on assert
    /// failure, so no test leaks first-responder state into the next.
    private func hostInKeyWindow(_ view: UIView) {
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        let root = UIViewController()
        window.rootViewController = root
        view.frame = root.view.bounds
        root.view.addSubview(view)
        window.makeKeyAndVisible()
        addTeardownBlock { @MainActor in
            _ = view.resignFirstResponder()
            window.isHidden = true
        }
    }

    // MARK: - MobaideTerminalView responder gate

    func testBecomeFirstResponderFailsWhileGateClosed() {
        let terminal = makeTerminal()
        hostInKeyWindow(terminal)
        var transitions: [Bool] = []
        terminal.onFirstResponderChange = { transitions.append($0) }

        XCTAssertFalse(
            terminal.becomeFirstResponder(),
            "with the gate closed no raise may succeed — even from inside a key window"
        )
        XCTAssertFalse(terminal.isFirstResponder)
        XCTAssertEqual(transitions, [], "a refused raise must not report a transition")
    }

    func testFailedRaiseClosesGate() {
        // Not in any window → UIKit refuses the raise even with the gate open.
        let terminal = makeTerminal()
        terminal.keyboardGate = true
        var transitions: [Bool] = []
        terminal.onFirstResponderChange = { transitions.append($0) }

        XCTAssertFalse(terminal.becomeFirstResponder())

        XCTAssertFalse(terminal.keyboardGate, "a failed raise must fail the gate closed")
        XCTAssertEqual(transitions, [])
    }

    func testResignClosesGateAndReportsInactive() {
        let terminal = makeTerminal()
        hostInKeyWindow(terminal)
        var transitions: [Bool] = []
        terminal.onFirstResponderChange = { transitions.append($0) }
        terminal.keyboardGate = true
        XCTAssertTrue(terminal.becomeFirstResponder())
        XCTAssertEqual(transitions, [true])

        XCTAssertTrue(terminal.resignFirstResponder())

        XCTAssertFalse(terminal.isFirstResponder)
        XCTAssertFalse(terminal.keyboardGate, "resign must close the gate so a later tap cannot re-raise")
        XCTAssertEqual(transitions, [true, false])
    }

    func testConfigureAppearanceRemovesSwiftTermAccessory() {
        let terminal = MobaideTerminalView(frame: .zero)
        XCTAssertNotNil(
            terminal.inputAccessoryView,
            "SwiftTerm's setup() installs its sticky-Ctrl TerminalAccessory at init — precondition for the removal below"
        )

        terminal.configureAppearance()

        XCTAssertNil(terminal.inputAccessoryView, "the app's control-key row replaces the accessory")
    }

    // MARK: - TerminalSessionModel keyboard plumbing

    func testToggleTerminalKeyboardTracksActiveState() {
        let model = TerminalTestSupport.model()
        hostInKeyWindow(model.terminalView)
        XCTAssertFalse(model.terminalKeyboardActive)

        model.toggleTerminalKeyboard()
        XCTAssertTrue(model.terminalKeyboardActive)
        XCTAssertTrue(model.terminalView.isFirstResponder)

        model.toggleTerminalKeyboard()
        XCTAssertFalse(model.terminalKeyboardActive)
        XCTAssertFalse(model.terminalView.isFirstResponder)
    }

    func testMarkHiddenDismissesTerminalKeyboard() {
        let model = TerminalTestSupport.model()
        hostInKeyWindow(model.terminalView)
        model.showTerminalKeyboard()
        XCTAssertTrue(model.terminalKeyboardActive)

        model.markHidden()

        XCTAssertFalse(
            model.terminalKeyboardActive,
            "leaving the screen must drop the keyboard — the retained view would swallow keys behind the next screen"
        )
        XCTAssertFalse(model.terminalView.isFirstResponder)
    }

    func testRevealComposerDismissesKeyboardAndEmitsFocus() async throws {
        let model = TerminalTestSupport.model()
        hostInKeyWindow(model.terminalView)
        model.hideComposer()
        model.showTerminalKeyboard()
        XCTAssertTrue(model.terminalKeyboardActive)
        let focusEvents = StreamCollector(model.focusRequests())

        model.revealComposer()

        XCTAssertTrue(model.composerVisible)
        XCTAssertFalse(
            model.terminalKeyboardActive,
            "the terminal responder must drop so keystrokes cannot keep routing into the PTY"
        )
        XCTAssertFalse(model.terminalView.isFirstResponder)
        let focused = try await focusEvents.next()
        XCTAssertTrue(focused, "revealing claims the field — the same stream releases it with false")
    }
}
