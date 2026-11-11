import Foundation
import SwiftTerm
import XCTest
@testable import Mobaide

/// An arbitrary monotonic instant. The scanner never reads a clock of its own,
/// so every deadline below is expressed relative to this.
private let sampleTime: TimeInterval = 1_000

/// The DECSET/DECRST 2026 scanner that decides whether the emulator may be
/// drawing a frozen snapshot instead of the buffer a row lookup reads.
///
/// Every case here is an instance of the asymmetry `TerminalSynchronizedOutput`
/// documents: arming may over-fire, clearing may not.
final class TerminalSynchronizedOutputTests: XCTestCase {
    private func armed(_ bytes: [UInt8]) -> Bool {
        var scanner = TerminalSynchronizedOutput()
        scanner.scan(bytes[...], at: sampleTime)
        return scanner.isActive(at: sampleTime)
    }

    private func armed(_ text: String) -> Bool {
        armed(Array(text.utf8))
    }

    private func scanned(_ text: String, at now: TimeInterval) -> TerminalSynchronizedOutput {
        var scanner = TerminalSynchronizedOutput()
        scanner.scan(Array(text.utf8)[...], at: now)
        return scanner
    }

    /// True when the given bytes leave an already-armed window open.
    private func survivesTheWindow(_ bytes: [UInt8]) -> Bool {
        var scanner = scanned("\u{1B}[?2026h", at: sampleTime)
        scanner.scan(bytes[...], at: sampleTime)
        return scanner.isActive(at: sampleTime)
    }

    private func survivesTheWindow(_ text: String) -> Bool {
        survivesTheWindow(Array(text.utf8))
    }

    // MARK: - Recognized sequences

    func testPlainTranscriptNeverArms() {
        XCTAssertFalse(
            armed("> npm test\r\nok, 2026 assertions, all h\r\n"),
            "the digits and the final byte both appear in ordinary output — only a control sequence may arm"
        )
    }

    func testDecsetArmsAndDecrstClearsImmediately() {
        var scanner = scanned("\u{1B}[?2026h", at: sampleTime)
        XCTAssertTrue(scanner.isActive(at: sampleTime))

        scanner.scan(Array("\u{1B}[?2026l".utf8)[...], at: sampleTime)

        XCTAssertFalse(
            scanner.isActive(at: sampleTime),
            "SwiftTerm answers DECRQM 2026 as supported, so Ink brackets every frame — a reset that "
                + "only shortened the window would leave row taps dead for the whole session"
        )
    }

    func testSequenceSplitAcrossTwoScansStillMatches() {
        var scanner = scanned("\u{1B}[?20", at: sampleTime)
        XCTAssertFalse(scanner.isActive(at: sampleTime), "half a sequence is not a set")

        scanner.scan(Array("26h".utf8)[...], at: sampleTime)

        XCTAssertTrue(
            scanner.isActive(at: sampleTime),
            "a WebSocket frame boundary is not a sequence boundary — the state has to survive the call"
        )
    }

    // MARK: - The gate still opens

    /// One Claude Code frame: BSU, a cursor-addressed repaint, ESU. SwiftTerm
    /// answers DECRQM 2026 as supported, so Ink's auto-detection brackets every
    /// frame like this — a scanner that never disarmed would satisfy every
    /// fail-closed assertion above while killing row taps for whole sessions.
    private static let inkFrame = Array("\u{1B}[?2026h\u{1B}[H\u{1B}[2K❯ npm test\u{1B}[?25l\u{1B}[?2026l".utf8)

    func testAnInkFrameSplitAcrossTwoScansLeavesTheGateClear() {
        let split = Self.inkFrame.count - 3
        var scanner = TerminalSynchronizedOutput()
        scanner.scan(Self.inkFrame[..<split], at: sampleTime)
        XCTAssertTrue(scanner.isActive(at: sampleTime), "the frame is still open at the split")

        scanner.scan(Self.inkFrame[split...], at: sampleTime)

        XCTAssertFalse(
            scanner.isActive(at: sampleTime),
            "a WebSocket frame boundary lands anywhere, including inside the closing `ESC[?2026l` — "
                + "state that does not survive the call leaves the gate shut for a frame nobody froze"
        )
    }

    func testConsecutiveInkFramesLeaveTheGateClear() {
        var scanner = TerminalSynchronizedOutput()

        for frame in 0..<10 {
            let now = sampleTime + Double(frame) * 0.05
            scanner.scan(Self.inkFrame[...], at: now)
            XCTAssertFalse(
                scanner.isActive(at: now),
                "frame \(frame): each frame re-arms a 1.25 s window, so one frame that failed to clear "
                    + "would keep every later one shut too"
            )
        }
    }

    // MARK: - Sequences that must stay inert

    func testDecrqmQueryIsInert() {
        XCTAssertFalse(
            armed("\u{1B}[?2026$p"),
            "the `$` intermediate and the `p` final make this the question Claude Code asks before "
                + "using the mode, not an answer to it"
        )
    }

    func testOtherPrivateFinalsAreInert() {
        for final in ["p", "r", "s"] {
            XCTAssertFalse(armed("\u{1B}[?2026\(final)"), "only `h` and `l` reach cmdSetMode/cmdResetMode")
        }
    }

    func testNonPrivateSetModeIsInert() {
        XCTAssertFalse(armed("\u{1B}[2026h"), "without the `?` collect this is an ANSI mode, not a DEC private one")
    }

    func testSetWithAnythingButABarePrivateMarkerIsInert() {
        XCTAssertFalse(
            armed("\u{1B}[?2026$h"),
            "`setMode` compares the whole collect for equality with `?`, so the `$` makes this a no-op "
                + "in the emulator — arming on it would be an invisible way to switch row taps off"
        )
        XCTAssertFalse(armed("\u{1B}[?>2026h"), "and a second prefix byte fails the same comparison")
    }

    func testSubstituteAbortsTheSequence() {
        XCTAssertFalse(
            armed(Array("\u{1B}[?2026".utf8) + [0x1A] + Array("h".utf8)),
            "SUB is the one C0 byte SwiftTerm keeps as a global abort, so the `h` behind it dispatches nothing"
        )
    }

    func testResetOfAnotherModeLeavesTheWindowOpen() {
        XCTAssertTrue(
            survivesTheWindow("\u{1B}[?1049l"),
            "a TUI leaving the alternate screen mid-freeze must not be read as ending the freeze"
        )
    }

    func testBoxDrawingBytesThatCarryACsiIntroducerStayInert() {
        XCTAssertFalse(
            armed("\u{251B} build finished\r\n"),
            "`┛` is E2 94 9B, so accepting 0x9B unconditionally starts a sequence here — it must die at "
                + "the next final byte instead of arming on ordinary output"
        )
    }

    // MARK: - Fail-open proofs of concept

    func testGroundIntroducerMayArmButNeverDisarms() {
        XCTAssertTrue(
            armed([0x9B] + Array("?2026h".utf8)),
            "SwiftTerm prints a ground 0x9B rather than starting a sequence, so this arms only the gate — "
                + "the deliberate over-approximation that buys not having to model OSC, DCS and APC"
        )
        XCTAssertTrue(
            survivesTheWindow([0x9B] + Array("?2026l".utf8)),
            "and the same over-approximation the other way round would hand a frozen display back to row "
                + "taps one frame after freezing it"
        )
    }

    func testParameterThatMerelyContains2026StaysInert() {
        XCTAssertFalse(armed("\u{1B}[?12026h"), "the parameter is compared as a number, not searched as text")
    }

    // MARK: - Expiry

    func testWindowOutlivesTheEmulatorsOwnSnapshotTimeout() {
        let scanner = scanned("\u{1B}[?2026h", at: sampleTime)
        let deadline = sampleTime + TerminalSynchronizedOutput.emulatorTimeout
            + TerminalSynchronizedOutput.expiryGrace

        XCTAssertTrue(
            scanner.isActive(at: deadline - 0.001),
            "SwiftTerm expires its snapshot from a main-queue work item that can be preceded by a tap "
                + "already queued behind it, so the gate has to outlive the snapshot rather than race it"
        )
        XCTAssertFalse(scanner.isActive(at: deadline))
    }

    func testReArmingExtendsTheDeadline() {
        var scanner = scanned("\u{1B}[?2026h", at: sampleTime)
        let firstDeadline = sampleTime + TerminalSynchronizedOutput.emulatorTimeout
            + TerminalSynchronizedOutput.expiryGrace

        scanner.scan(Array("\u{1B}[?2026h".utf8)[...], at: sampleTime + 0.9)

        XCTAssertTrue(
            scanner.isActive(at: firstDeadline + 0.05),
            "output that re-arms every half second freezes the display indefinitely, and SwiftTerm "
                + "reschedules its expiry without taking a new snapshot"
        )
        XCTAssertFalse(scanner.isActive(at: firstDeadline + 0.9))
    }

    // MARK: - Differential sweep against the real emulator

    /// Collects SwiftTerm's own DECRQM reply. The emulator answers mode 2026
    /// from the very flag that decides whether it is drawing a snapshot, so it
    /// is the only oracle that cannot be argued with.
    private final class DecrqmProbe: TerminalDelegate {
        var reply = ""

        func send(source: Terminal, data: ArraySlice<UInt8>) {
            reply += String(decoding: data, as: UTF8.self)
        }
    }

    private struct DifferentialCase {
        let name: String
        /// Fed to both sides before `body`, to reach the state the direction
        /// under test starts from.
        let prefix: [UInt8]
        let body: [UInt8]
        /// Only the named regressions claim what the emulator itself does — a
        /// swept byte claims nothing beyond its direction's invariant, since
        /// over-firing is allowed in one direction and never in the other.
        var emulatorFreezes: Bool?
    }

    private enum EmulatorAnswer {
        case frozen
        case live
        /// No `$y` report at all: the oracle, not the scanner, is broken.
        case unanswered
    }

    private static let armingSequence = Array("\u{1B}[?2026h".utf8)
    private static let decrqmQuery = Array("\u{1B}[?2026$p".utf8)

    private static func swept(
        _ name: String, prefix: [UInt8] = [], _ body: (UInt8) -> [UInt8]
    ) -> [DifferentialCase] {
        (UInt8(0)...UInt8(255)).map { byte in
            DifferentialCase(
                name: name + String(format: "  b=0x%02X", Int(byte)),
                prefix: prefix,
                body: body(byte)
            )
        }
    }

    /// All 65,536 pairs need about a minute against a real `Terminal` and get
    /// the test runner killed, so both swept bytes are drawn from the classes
    /// that can move either machine between states: C0 and DEL, the CSI
    /// prefix/separator bytes, and the C1 range holding `0x9B`. Digits,
    /// intermediates and finals are swept at their own positions by `swept`,
    /// and from ground they only print.
    private static let stateChangingBytes: [UInt8] =
        Array(UInt8(0x00)...UInt8(0x20)) + Array(UInt8(0x3A)...UInt8(0x3F)) + [0x7F]
        + Array(UInt8(0x80)...UInt8(0xA0))

    /// `swept` varies one byte, so its corpus never leaves Hamming distance 1 of
    /// the reference spelling. A divergence planted at the introducer takes two:
    /// one byte to put the scanner in a CSI state SwiftTerm never entered, a
    /// second to open the sequence that clears the gate from inside it.
    private static func sweptPair(
        _ name: String, prefix: [UInt8] = [], _ body: (UInt8, UInt8) -> [UInt8]
    ) -> [DifferentialCase] {
        stateChangingBytes.flatMap { first in
            stateChangingBytes.map { second in
                DifferentialCase(
                    name: name + String(format: "  b=0x%02X,0x%02X", Int(first), Int(second)),
                    prefix: prefix,
                    body: body(first, second)
                )
            }
        }
    }

    /// Every byte at every position of the arming sequence, which is the search
    /// a hostile program runs: one spelling the emulator honours and the gate
    /// misses is a tap that executes a line the user never saw.
    private static let armCases: [DifferentialCase] =
        swept("ESC <b> [?2026h") { [0x1B, $0] + Array("[?2026h".utf8) }
        + swept("ESC[ <b> ?2026h") { [0x1B, 0x5B, $0] + Array("?2026h".utf8) }
        + swept("ESC[? <b> 2026h") { [0x1B, 0x5B, 0x3F, $0] + Array("2026h".utf8) }
        + swept("ESC[?20 <b> 26h") { [0x1B, 0x5B, 0x3F, 0x32, 0x30, $0] + Array("26h".utf8) }
        + swept("ESC[?2026 <b> h") { [0x1B, 0x5B] + Array("?2026".utf8) + [$0, 0x68] }
        + swept("<b> [?2026h") { [$0] + Array("[?2026h".utf8) }
        + swept("ESC <b> ESC[?2026h") { [0x1B, $0] + Array("\u{1B}[?2026h".utf8) }
        + [
            DifferentialCase(
                name: "PoC: ESC ] 0x9B ?2026h",
                prefix: [],
                body: Array("\u{1B}]".utf8) + [0x9B] + Array("?2026h".utf8),
                emulatorFreezes: true
            ),
            DifferentialCase(
                name: "PoC: parameter overflows and then spells 2026",
                prefix: [],
                body: Array("\u{1B}[?1234567890123456789".utf8) + Array("02026h".utf8),
                emulatorFreezes: true
            ),
            DifferentialCase(
                name: "ESC[?1049;2026h",
                prefix: [],
                body: Array("\u{1B}[?1049;2026h".utf8),
                emulatorFreezes: true
            ),
            DifferentialCase(
                name: "ESC[?2026;1h",
                prefix: [],
                body: Array("\u{1B}[?2026;1h".utf8),
                emulatorFreezes: true
            ),
            DifferentialCase(
                name: "DECRQM ESC[?2026$p",
                prefix: [],
                body: Array("\u{1B}[?2026$p".utf8),
                emulatorFreezes: false
            ),
            DifferentialCase(
                name: "non-private ESC[2026h",
                prefix: [],
                body: Array("\u{1B}[2026h".utf8),
                emulatorFreezes: false
            ),
        ]

    /// The same sweep from a genuinely frozen emulator, which is the direction
    /// the gate exists for: a clearing spelling the gate believes and the
    /// emulator ignores hands row taps back while the display is still frozen,
    /// one extra frame after freezing it.
    private static let resetCases: [DifferentialCase] =
        swept("ESC <b> [?2026l", prefix: armingSequence) { [0x1B, $0] + Array("[?2026l".utf8) }
        + swept("ESC[ <b> ?2026l", prefix: armingSequence) { [0x1B, 0x5B, $0] + Array("?2026l".utf8) }
        + swept("ESC[? <b> 2026l", prefix: armingSequence) { [0x1B, 0x5B, 0x3F, $0] + Array("2026l".utf8) }
        + swept("ESC[?2026 <b> l", prefix: armingSequence) { [0x1B, 0x5B] + Array("?2026".utf8) + [$0, 0x6C] }
        + swept("<b> ?2026l", prefix: armingSequence) { [$0] + Array("?2026l".utf8) }
        + [
            DifferentialCase(
                name: "the real ESC[?2026l",
                prefix: armingSequence,
                body: Array("\u{1B}[?2026l".utf8),
                emulatorFreezes: false
            ),
            DifferentialCase(
                name: "PoC: ground 0x9B ?2026l",
                prefix: armingSequence,
                body: [0x9B] + Array("?2026l".utf8),
                emulatorFreezes: true
            ),
            DifferentialCase(
                name: "PoC: ESC[1;?2026l",
                prefix: armingSequence,
                body: Array("\u{1B}[1;?2026l".utf8),
                emulatorFreezes: true
            ),
            DifferentialCase(
                name: "PoC: ESC[?2026$l",
                prefix: armingSequence,
                body: Array("\u{1B}[?2026$l".utf8),
                emulatorFreezes: true
            ),
            DifferentialCase(
                name: "PoC: ESC[??2026l",
                prefix: armingSequence,
                body: Array("\u{1B}[??2026l".utf8),
                emulatorFreezes: true
            ),
            DifferentialCase(
                name: "PoC: ESC[2026?l",
                prefix: armingSequence,
                body: Array("\u{1B}[2026?l".utf8),
                emulatorFreezes: true
            ),
        ]
        + sweptPair("<b0><b1> ?2026l", prefix: armingSequence) { [$0, $1] + Array("?2026l".utf8) }
        + doubleIntroducerCases

    /// A ground `0x9B` leaves the scanner in `csiEntry` while SwiftTerm is still
    /// in ground, and the whole class of bypasses follows from reading the
    /// scanner's own state back as if it were the emulator's: a second `0x9B`
    /// looks like it arrived from inside a control sequence, so the taint has to
    /// be carried rather than recomputed. Only an `ESC` puts the two machines
    /// back in step, and none of these contain one.
    private static let doubleIntroducerCases: [DifferentialCase] = [
        DifferentialCase(
            name: "PoC: 0x9B 0x9B ?2026l",
            prefix: armingSequence,
            body: [0x9B, 0x9B] + Array("?2026l".utf8),
            emulatorFreezes: true
        ),
        DifferentialCase(
            name: "PoC: 0x9B ? 0x9B ?2026l",
            prefix: armingSequence,
            body: [0x9B, 0x3F, 0x9B] + Array("?2026l".utf8),
            emulatorFreezes: true
        ),
        DifferentialCase(
            name: "PoC: 0x9B CAN 0x9B ?2026l",
            prefix: armingSequence,
            body: [0x9B, 0x18, 0x9B] + Array("?2026l".utf8),
            emulatorFreezes: true
        ),
        DifferentialCase(
            name: "PoC: [ 0x9B 0x9B ?2026l",
            prefix: armingSequence,
            body: [0x5B, 0x9B, 0x9B] + Array("?2026l".utf8),
            emulatorFreezes: true
        ),
        DifferentialCase(
            name: "PoC: ┛ 0x9B ?2026l",
            prefix: armingSequence,
            body: Array("\u{251B}".utf8) + [0x9B] + Array("?2026l".utf8),
            emulatorFreezes: true
        ),
    ]

    private func emulatorAnswer(_ testCase: DifferentialCase) -> EmulatorAnswer {
        let probe = DecrqmProbe()
        // Default geometry, but no scrollback: kept from when every armed case
        // deep-copied a 525-line buffer in `beginSynchronizedOutput`, which made
        // the sweep heavy enough to get the test host jettisoned. SwiftTerm 1.15
        // suppresses paints instead of snapshotting, so the copy is gone, but a
        // 525-line buffer per case is still weight the sweep does not need. DECRQM
        // answers 2026 straight off the mode flag, so the reply is unaffected —
        // verified identical over both sweeps' full 0x00-0xFF pair space.
        let terminal = Terminal(delegate: probe, options: TerminalOptions(cols: 80, rows: 25, scrollback: 0))
        if !testCase.prefix.isEmpty { terminal.feed(byteArray: testCase.prefix) }
        terminal.feed(byteArray: testCase.body)
        probe.reply = ""
        terminal.feed(byteArray: Self.decrqmQuery)
        if probe.reply.contains("\u{1B}[?2026;1$y") { return .frozen }
        if probe.reply.contains("\u{1B}[?2026;2$y") { return .live }
        return .unanswered
    }

    /// The scanner is never shown the DECRQM query the oracle needs: its `ESC`
    /// would abort exactly the half-finished sequences these cases are made of.
    private func scannerIsArmed(_ testCase: DifferentialCase) -> Bool {
        var scanner = TerminalSynchronizedOutput()
        if !testCase.prefix.isEmpty { scanner.scan(testCase.prefix[...], at: sampleTime) }
        scanner.scan(testCase.body[...], at: sampleTime)
        return scanner.isActive(at: sampleTime)
    }

    func testNoSpellingFreezesTheEmulatorWithoutArmingTheGate() {
        var failedOpen: [String] = []
        var unanswered: [String] = []
        var frozenCases = 0

        for testCase in Self.armCases {
            let answer = emulatorAnswer(testCase)
            if let claimed = testCase.emulatorFreezes {
                XCTAssertEqual(
                    answer == .frozen, claimed,
                    "\(testCase.name): the emulator's own answer is what this case claims"
                )
            }
            switch answer {
            case .frozen:
                frozenCases += 1
                if !scannerIsArmed(testCase) { failedOpen.append(testCase.name) }
            case .live:
                break
            case .unanswered:
                unanswered.append(testCase.name)
            }
        }

        XCTAssertEqual(unanswered, [], "DECRQM is the oracle — a case it does not answer proves nothing")
        XCTAssertGreaterThan(frozenCases, 0, "a sweep the emulator never froze in would pass while asserting nothing")
        XCTAssertEqual(
            failedOpen, [],
            "each of these froze the real emulator without arming the gate: a tap taken here resolves a "
                + "row out of a buffer nobody is drawing. Over-firing the other way is allowed and expected."
        )
    }

    func testNoSpellingReopensTheGateWhileTheEmulatorStaysFrozen() {
        var reopened: [String] = []
        var unanswered: [String] = []
        var overApproximating: [String] = []

        for testCase in Self.resetCases {
            let answer = emulatorAnswer(testCase)
            let armed = scannerIsArmed(testCase)
            if let claimed = testCase.emulatorFreezes {
                XCTAssertEqual(
                    answer == .frozen, claimed,
                    "\(testCase.name): the emulator's own answer is what this case claims"
                )
            }
            switch answer {
            case .frozen where !armed:
                reopened.append(testCase.name)
            case .live where armed:
                overApproximating.append(testCase.name)
            case .unanswered:
                unanswered.append(testCase.name)
            default:
                break
            }
        }

        XCTAssertEqual(unanswered, [], "DECRQM is the oracle — a case it does not answer proves nothing")
        XCTAssertEqual(
            reopened, [],
            "each of these handed row taps back while SwiftTerm was still drawing its snapshot — the "
                + "one-extra-frame attack the gate exists to stop"
        )
        XCTAssertEqual(
            overApproximating, ["ESC <b> [?2026l  b=0x63"],
            "0x63 is `c`: RIS, which ends synchronized output without a `2026l` ever reaching the stream, "
                + "so the gate stays shut for up to 1.25 s longer than the emulator. Fail-closed, and the "
                + "only divergence there is — anything else listed here needs explaining, and the real "
                + "`ESC[?2026l` showing up would mean the gate never reopens at all."
        )
    }
}
