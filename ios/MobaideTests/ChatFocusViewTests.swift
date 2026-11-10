import XCTest
@testable import Mobaide

/// `ChatFocusView.rows` (web `focusView.test.ts`, Android `ChatFocusViewTest`):
/// the work between a question and its answer folds into one steps row per
/// run; questions, answers, waiting tool calls and status lines stay in place.
final class ChatFocusViewTests: XCTestCase {
    private func user(_ id: String) -> ChatItem {
        ChatItem(id: id, body: .user(text: "Fix the login bug", attachments: []))
    }

    private func agent(_ id: String) -> ChatItem {
        ChatItem(id: id, body: .agent(text: "Done"))
    }

    private func thought(_ id: String) -> ChatItem {
        ChatItem(id: id, body: .thought(text: "Reading the code"))
    }

    private func tool(_ id: String, title: String = "Run tests") -> ChatItem {
        ChatItem(id: id, body: .tool(ChatToolCall(
            title: title,
            kind: "execute",
            status: "in_progress",
            subtitle: nil,
            locations: [],
            content: [],
            input: nil
        )))
    }

    private func notice(_ id: String) -> ChatItem {
        ChatItem(id: id, body: .notice(severity: "info", title: "Loaded", text: ""))
    }

    private func compaction(_ id: String) -> ChatItem {
        ChatItem(id: id, body: .compaction(status: "completed", text: ""))
    }

    private func error(_ id: String) -> ChatItem {
        ChatItem(id: id, body: .error(text: "The agent exited"))
    }

    private func turnEnd(_ id: String) -> ChatItem {
        ChatItem(id: id, body: .turnEnd(stopReason: "end_turn", text: ""))
    }

    private func rows(
        _ items: [ChatItem],
        focus: Bool = true,
        running: Bool = false,
        waiting: Set<String> = [],
        open: Set<String> = []
    ) -> [ChatTimelineRow] {
        ChatFocusView.rows(items, focus: focus, running: running, waitingToolIds: waiting, open: open)
    }

    /// Item rows by id (nested ones indented), steps rows as `<id> ×<count>`.
    private func outline(_ rows: [ChatTimelineRow]) -> [String] {
        rows.map { row in
            switch row {
            case .item(let item, let nested): return nested ? "  \(item.id)" : item.id
            case .steps(let id, let count, _, _, _): return "\(id) ×\(count)"
            }
        }
    }

    private struct Steps: Equatable {
        let id: String
        let count: Int
        let live: Bool
        let current: String?
        let open: Bool
    }

    private func steps(_ rows: [ChatTimelineRow]) -> [Steps] {
        rows.compactMap { row in
            guard case .steps(let id, let count, let live, let current, let open) = row else { return nil }
            return Steps(id: id, count: count, live: live, current: current, open: open)
        }
    }

    func testFocusOffShowsEveryItemInPlace() {
        let items = [user("u:1"), thought("t:2"), tool("tool-3"), agent("m:4")]

        XCTAssertEqual(rows(items, focus: false, running: true), items.map { .item($0, nested: false) })
    }

    func testTheWorkOfATurnFoldsIntoOneStepsRowBetweenTheQuestionAndTheAnswer() {
        let result = rows([user("u:1"), thought("t:2"), agent("m:3"), tool("tool-4"), agent("m:5"), turnEnd("end:6")])

        XCTAssertEqual(outline(result), ["u:1", "steps:t:2 ×3", "m:5", "end:6"])
    }

    func testTheAnswerOfATurnStaysInPlaceWhenTheNextTurnDoesMoreWork() {
        let result = rows([user("u:1"), tool("tool-2"), agent("m:3"), user("u:4"), tool("tool-5"), agent("m:6")])

        XCTAssertEqual(outline(result), ["u:1", "steps:tool-2 ×1", "m:3", "u:4", "steps:tool-5 ×1", "m:6"])
    }

    func testAToolWaitingForApprovalStaysInPlaceAndSplitsTheRun() {
        let result = rows([user("u:1"), thought("t:2"), tool("tool-3"), tool("tool-4")], waiting: ["tool-3"])

        XCTAssertEqual(outline(result), ["u:1", "steps:t:2 ×1", "tool-3", "steps:tool-4 ×1"])
    }

    func testNoticesCompactionAndErrorsStayInPlaceBetweenRuns() {
        let result = rows([user("u:1"), thought("t:2"), notice("n:3"), tool("tool-4"), compaction("cmp-5"), error("err:6")])

        XCTAssertEqual(outline(result), ["u:1", "steps:t:2 ×1", "n:3", "steps:tool-4 ×1", "cmp-5", "err:6"])
    }

    func testANoticeEndsATurnLikeAUserMessageSoTheAnswerBeforeItStaysInPlace() {
        let fromAnotherApp = rows([user("u:1"), agent("m:2"), notice("n:3"), tool("call_1"), agent("m:4")])
        XCTAssertEqual(outline(fromAnotherApp), ["u:1", "m:2", "n:3", "steps:call_1 ×1", "m:4"])

        let midRun = rows([user("u:1"), thought("t:2"), agent("m:3"), notice("n:4"), tool("tool-5"), agent("m:6")])
        XCTAssertEqual(outline(midRun), ["u:1", "steps:t:2 ×1", "m:3", "n:4", "steps:tool-5 ×1", "m:6"])
    }

    func testTheRunThatEndsARunningTurnNamesItsNewestStep() {
        let items = [user("u:1"), thought("t:2"), agent("m:3"), tool("tool-4"), thought("t:5"), tool("tool-6", title: "pytest -q")]

        XCTAssertEqual(
            steps(rows(items, running: true)),
            [Steps(id: ChatFocusView.stepsId(firstItemId: "t:2"), count: 5, live: true, current: "pytest -q", open: false)]
        )
        XCTAssertEqual(steps(rows(Array(items.prefix(5)), running: true)).map(\.current), ["Thinking"])
        XCTAssertEqual(steps(rows([user("u:1"), tool("tool-2", title: "")], running: true)).map(\.current), ["Tool call"])
    }

    func testNoRunIsLiveWhenTheTurnIsNotRunningOrTheRunIsNotTheNewestThing() {
        let working = [user("u:1"), thought("t:2"), tool("tool-3")]

        let idle = steps(rows(working))
        XCTAssertEqual(idle.map(\.live), [false])
        XCTAssertEqual(idle.map(\.current), [nil])

        let answered = steps(rows(working + [agent("m:4")], running: true))
        XCTAssertEqual(answered.map(\.live), [false])
        XCTAssertEqual(answered.map(\.current), [nil])

        let twoRuns = steps(rows([user("u:1"), thought("t:2"), notice("n:3"), tool("tool-4")], running: true))
        XCTAssertEqual(twoRuns.map(\.live), [false, true])
    }

    func testAnOpenStepsRowIsFollowedByItsStepsNested() {
        let items = [
            user("u:1"), thought("t:2"), agent("m:3"), tool("tool-4"), agent("m:5"),
            user("u:6"), tool("tool-7"), agent("m:8"),
        ]

        let result = rows(items, open: [ChatFocusView.stepsId(firstItemId: "t:2")])

        XCTAssertEqual(
            outline(result),
            ["u:1", "steps:t:2 ×3", "  t:2", "  m:3", "  tool-4", "m:5", "u:6", "steps:tool-7 ×1", "m:8"]
        )
        XCTAssertEqual(steps(result).map(\.open), [true, false])
    }

    func testStepsLabelCountsOneAndMany() {
        XCTAssertEqual(ChatFocusView.stepsLabel(1), "1 step")
        XCTAssertEqual(ChatFocusView.stepsLabel(4), "4 steps")
    }
}
