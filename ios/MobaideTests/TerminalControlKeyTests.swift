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

    func testCodexKindUsesCodexSet() {
        XCTAssertEqual(ids(TerminalControlKey.keys(for: .codex)), ids(TerminalControlKey.codexKeys))
    }

    func testAntigravityKindUsesAntigravitySet() {
        XCTAssertEqual(ids(TerminalControlKey.keys(for: .antigravity)), ids(TerminalControlKey.antigravityKeys))
    }

    func testClaudeShellAndAutoScanFallThroughToClaudeSet() {
        let claudeIds = ids(TerminalControlKey.claudeKeys)
        XCTAssertEqual(ids(TerminalControlKey.keys(for: .claude)), claudeIds)
        XCTAssertEqual(ids(TerminalControlKey.keys(for: .shell)), claudeIds,
                       "shell has no bespoke set — it must fall through to the Claude keys")
        XCTAssertEqual(ids(TerminalControlKey.keys(for: .autoScan)), claudeIds)
    }

    // MARK: - Set composition (order is what the user sees + the fixed wire order)

    func testClaudeSetComposition() {
        XCTAssertEqual(
            ids(TerminalControlKey.claudeKeys),
            ["enter", "up", "down", "left", "right", "ctrlC", "mode", "ctrlL", "esc",
             "rewind", "tab", "backspace", "ctrlO", "ctrlR"]
        )
    }

    func testCodexSetInsertsApproveRejectAfterFixedPrefix() {
        XCTAssertEqual(
            ids(TerminalControlKey.codexKeys),
            ["enter", "up", "down", "left", "right", "ctrlC", "mode", "ctrlL", "esc",
             "approve", "reject", "rewind", "tab", "backspace", "ctrlO", "ctrlR"]
        )
    }

    func testAntigravitySetComposition() {
        XCTAssertEqual(
            ids(TerminalControlKey.antigravityKeys),
            ["enter", "up", "down", "left", "right", "ctrlC", "ctrlL", "esc",
             "ctrlK", "ctrlG", "ctrlD", "tab", "backspace", "ctrlO", "ctrlR"]
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
}
