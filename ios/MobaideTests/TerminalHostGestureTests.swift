import UIKit
import XCTest
@testable import Mobaide

@MainActor
final class TerminalHostGestureTests: XCTestCase {
    private final class PanSubclass: UIPanGestureRecognizer {}

    private func makeModel() -> TerminalSessionModel {
        TerminalTestSupport.model()
    }

    private func makeTerminal() -> MobaideTerminalView {
        MobaideTerminalView(frame: .zero)
    }

    private func recognizers(named name: String, on view: UIView) -> [UIGestureRecognizer] {
        (view.gestureRecognizers ?? []).filter { $0.name == name }
    }

    // MARK: - neutralizeSwiftTermGestures

    func testDisablesPlainPanButKeepsScrollViewPan() {
        let terminal = makeTerminal()
        // Simulates the recognizer SwiftTerm adds lazily while a TUI enables
        // mouse reporting; it swallowed every drag in Claude sessions.
        let mouseReportPan = UIPanGestureRecognizer()
        terminal.addGestureRecognizer(mouseReportPan)
        let scrollbackDriver = terminal.panGestureRecognizer

        terminal.neutralizeSwiftTermGestures()

        XCTAssertFalse(mouseReportPan.isEnabled, "the mouse-reporting pan must be disabled")
        XCTAssertTrue(scrollbackDriver.isEnabled, "the scrollback pan must stay alive")
    }

    func testExactClassCheckSparesPanSubclasses() {
        let terminal = makeTerminal()
        let subclassPan = PanSubclass()
        terminal.addGestureRecognizer(subclassPan)

        terminal.neutralizeSwiftTermGestures()

        XCTAssertTrue(
            subclassPan.isEnabled,
            "only exactly UIPanGestureRecognizer is neutralized — subclasses like UIScrollViewPanGestureRecognizer must survive"
        )
    }

    func testDisablesAllSwiftTermTapsAndSelectionGesturesButKeepsMobaideRecognizers() {
        let terminal = makeTerminal()
        let longPress = UILongPressGestureRecognizer()
        let tripleTap = UITapGestureRecognizer()
        tripleTap.numberOfTapsRequired = 3
        let singleTap = UITapGestureRecognizer()
        singleTap.numberOfTapsRequired = 1
        let mobaideDoubleTap = UITapGestureRecognizer()
        mobaideDoubleTap.numberOfTapsRequired = 2
        mobaideDoubleTap.name = "mobaide.rowDoubleTap"
        for recognizer in [longPress, tripleTap, singleTap, mobaideDoubleTap] {
            terminal.addGestureRecognizer(recognizer)
        }

        terminal.neutralizeSwiftTermGestures()

        XCTAssertFalse(longPress.isEnabled)
        XCTAssertFalse(tripleTap.isEnabled)
        XCTAssertFalse(
            singleTap.isEnabled,
            "SwiftTerm's single-tap calls becomeFirstResponder — every SwiftTerm tap must be disabled"
        )
        XCTAssertTrue(mobaideDoubleTap.isEnabled, "mobaide.* recognizers are exempt")
    }

    func testMouseModeChangedRerunsNeutralization() {
        let terminal = makeTerminal()
        terminal.neutralizeSwiftTermGestures()
        // Added after the sweep, as SwiftTerm does on a mouse-mode transition.
        let latePan = UIPanGestureRecognizer()
        terminal.addGestureRecognizer(latePan)
        XCTAssertTrue(latePan.isEnabled)

        terminal.mouseModeChanged(source: terminal.getTerminal())

        XCTAssertFalse(latePan.isEnabled, "lazily added pans must be swept on mouse-mode changes")
    }

    // MARK: - Coordinator.attachGestures

    func testAttachGesturesReplacesStaleRecognizersFromPreviousVisit() {
        let model = makeModel()
        let terminal = model.terminalView

        let firstCoordinator = TerminalHostView.Coordinator(model: model)
        firstCoordinator.attachGestures(to: terminal)
        let firstTap = recognizers(named: "mobaide.rowTap", on: terminal).first
        XCTAssertNotNil(firstTap)

        let secondCoordinator = TerminalHostView.Coordinator(model: model)
        secondCoordinator.attachGestures(to: terminal)

        let taps = recognizers(named: "mobaide.rowTap", on: terminal)
        let doubleTaps = recognizers(named: "mobaide.rowDoubleTap", on: terminal)
        XCTAssertEqual(taps.count, 1, "re-attaching must not accumulate duplicates")
        XCTAssertEqual(doubleTaps.count, 1)
        XCTAssertFalse(
            taps[0] === firstTap,
            "the recognizer must be rebuilt so it targets the current Coordinator, not the first visit's"
        )
    }

    // MARK: - dismantleUIView

    func testDismantleDetachesTerminalWhenWrapperStillOwnsIt() {
        let model = makeModel()
        let coordinator = TerminalHostView.Coordinator(model: model)
        let config = TerminalDisplayConfig(mode: .static, staticColumns: 120)
        let wrapper = TerminalPanScrollView(terminalView: model.terminalView, displayConfig: config)

        TerminalHostView.dismantleUIView(wrapper, coordinator: coordinator)

        XCTAssertNil(model.terminalView.superview)
    }

    func testDismantleLeavesTerminalWithTheWrapperThatStoleIt() {
        let model = makeModel()
        let coordinator = TerminalHostView.Coordinator(model: model)
        let config = TerminalDisplayConfig(mode: .static, staticColumns: 120)
        let oldWrapper = TerminalPanScrollView(terminalView: model.terminalView, displayConfig: config)
        let newWrapper = TerminalPanScrollView(terminalView: model.terminalView, displayConfig: config)

        TerminalHostView.dismantleUIView(oldWrapper, coordinator: coordinator)

        XCTAssertTrue(
            model.terminalView.superview === newWrapper,
            "a dying wrapper must not detach a terminal a newer wrapper already owns"
        )
    }
}
