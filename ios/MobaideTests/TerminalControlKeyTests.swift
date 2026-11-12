import XCTest
@testable import Mobaide

/// Covers the per-kind control-key tables in `InputBar.swift`. The byte
/// sequences are wire load-bearing (fed verbatim as `i` frames), and
/// `keys(for:)` is the dispatch the InputBar renders — both must survive edits
/// to the key list unchanged.
final class TerminalControlKeyTests: XCTestCase {
    private func ids(_ keys: [TerminalControlKey]) -> [String] { keys.map(\.id) }

    private func bytes(_ id: String, in keys: [TerminalControlKey]) -> [UInt8]? {
        keys.first { $0.id == id }?.bytes
    }

    // MARK: - keys(for:) dispatch

    func testNilCustomAndShellKindsUseDefaultSet() {
        let defaultIds = ids(TerminalControlKey.defaultKeys)
        XCTAssertEqual(
            ids(TerminalControlKey.keys(for: nil)), defaultIds,
            "an unresolved kind renders the neutral set — never a guessed Claude bar"
        )
        XCTAssertEqual(ids(TerminalControlKey.keys(for: .custom("custom_ab12cd34ef56"))), defaultIds)
        XCTAssertEqual(
            ids(TerminalControlKey.keys(for: .shell)), defaultIds,
            "a bash prompt is not a Claude TUI — Mode/Rewind/Ctrl+O/Ctrl+R do not belong there"
        )
    }

    func testOpenCodeKindUsesTheNeutralSetPlusModeAndItsOwnKeys() {
        // Rewind, Ctrl+O/Ctrl+R/Ctrl+L, Approve/Reject and Antigravity's keys
        // are bound differently in the OpenCode TUI, or not at all.
        XCTAssertEqual(
            ids(TerminalControlKey.keys(for: .opencode)),
            ["enter", "up", "down", "left", "right", "ctrlC", "mode", "esc", "ctrlP", "tab", "backspace"]
        )
    }

    // MARK: - Set composition (order is what the user sees + the fixed wire order)

    func testDefaultSetCompositionOmitsClaudeTUIKeys() {
        // Nav + universal interrupt/erase only (Android DEFAULT_CONTROL_KEYS):
        // Mode/Rewind/Ctrl+O/Ctrl+R/Ctrl+L carry Claude-TUI meanings an
        // arbitrary CLI may bind differently, so they must not leak in.
        XCTAssertEqual(
            ids(TerminalControlKey.defaultKeys),
            ["enter", "up", "down", "left", "right", "ctrlC", "esc", "tab", "backspace"]
        )
    }

    // MARK: - Load-bearing byte sequences

    func testArrowEscapeSequences() {
        let claude = TerminalControlKey.claudeKeys
        XCTAssertEqual(bytes("up", in: claude), [0x1B, 0x5B, 0x41])
        XCTAssertEqual(bytes("down", in: claude), [0x1B, 0x5B, 0x42])
        XCTAssertEqual(bytes("left", in: claude), [0x1B, 0x5B, 0x44], "left is CSI D — not transposed with right")
        XCTAssertEqual(bytes("right", in: claude), [0x1B, 0x5B, 0x43], "right is CSI C — not transposed with left")
    }

    func testModeAndRewindEscapeSequences() {
        let claude = TerminalControlKey.claudeKeys
        XCTAssertEqual(bytes("mode", in: claude), [0x1B, 0x5B, 0x5A])
        XCTAssertEqual(bytes("rewind", in: claude), [0x1B, 0x1B])
    }

    func testBackspaceIsSharedAndSendsDEL() {
        // DEL (0x7F) — the erase byte readline/Claude Code/Codex expect —
        // never BS (0x08), and present for every kind (Android SHARED_KEYS).
        for keys in [TerminalControlKey.claudeKeys, TerminalControlKey.codexKeys, TerminalControlKey.antigravityKeys] {
            XCTAssertEqual(bytes("backspace", in: keys), [0x7F])
        }
    }

    func testCodexAndAntigravityControlBytes() {
        XCTAssertEqual(bytes("approve", in: TerminalControlKey.codexKeys), [0x79])
        XCTAssertEqual(bytes("reject", in: TerminalControlKey.codexKeys), [0x6E])
        XCTAssertEqual(bytes("ctrlC", in: TerminalControlKey.codexKeys), [0x03])
        XCTAssertEqual(bytes("ctrlK", in: TerminalControlKey.antigravityKeys), [0x0B])
        XCTAssertEqual(bytes("ctrlG", in: TerminalControlKey.antigravityKeys), [0x07])
        XCTAssertEqual(bytes("ctrlD", in: TerminalControlKey.antigravityKeys), [0x04])
    }

    func testOpenCodeControlBytes() {
        let opencode = TerminalControlKey.opencodeKeys
        XCTAssertEqual(bytes("ctrlP", in: opencode), [0x10], "the command palette")
        XCTAssertEqual(bytes("mode", in: opencode), [0x1B, 0x5B, 0x5A], "Shift+Tab cycles the agent")
        XCTAssertEqual(bytes("backspace", in: opencode), [0x7F])
    }
}
