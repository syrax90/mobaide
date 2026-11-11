import XCTest
@testable import Mobaide

/// The chat timeline reducer, fed decoded `a` frames: snapshot / events
/// application and the sync rules (duplicates, gaps, a missing tail, another
/// epoch) plus the client item cap, which drops old steps (tool calls,
/// thoughts) before messages.
final class ChatTimelineTests: XCTestCase {
    private func frame(_ json: String) throws -> ChatServerFrame {
        try XCTUnwrap(ChatServerFrame.decode(Data(json.utf8)), json)
    }

    private func snapshot(seq: Int, items: String = "[]", epoch: String = "e1") throws -> ChatServerFrame {
        try frame(#"{"t":"snapshot","epoch":"\#(epoch)","seq":\#(seq),"truncated":false,"state":{"status":"ready"},"items":\#(items)}"#)
    }

    private func events(_ events: String, seq: Int, epoch: String = "e1") throws -> ChatServerFrame {
        try frame(#"{"t":"events","epoch":"\#(epoch)","seq":\#(seq),"events":\#(events)}"#)
    }

    private func agent(_ id: String, _ text: String = "") -> String {
        #"{"id":"\#(id)","type":"agent","text":"\#(text)"}"#
    }

    private func user(_ id: String, _ text: String = "") -> String {
        #"{"id":"\#(id)","type":"user","text":"\#(text)"}"#
    }

    private func thought(_ id: String, _ text: String = "") -> String {
        #"{"id":"\#(id)","type":"thought","text":"\#(text)"}"#
    }

    private func tool(_ id: String, _ status: String = "pending") -> String {
        #"{"id":"\#(id)","type":"tool","title":"Run tests","kind":"execute","status":"\#(status)"}"#
    }

    private func append(_ seq: Int, _ id: String, _ text: String) -> String {
        #"{"type":"append","seq":\#(seq),"id":"\#(id)","text":"\#(text)"}"#
    }

    private func loaded(seq: Int, items: String, maxItems: Int = ChatTimeline.defaultMaxItems) throws -> ChatTimeline {
        var timeline = ChatTimeline(maxItems: maxItems)
        _ = timeline.apply(try snapshot(seq: seq, items: items))
        return timeline
    }

    private func text(_ timeline: ChatTimeline, _ id: String) -> String? {
        guard let item = timeline.items.first(where: { $0.id == id }) else { return nil }
        switch item.body {
        case .agent(let text), .thought(let text), .error(let text): return text
        default: return nil
        }
    }

    func testSyncRequestIsFreshUntilASnapshotArrives() throws {
        var timeline = ChatTimeline()
        XCTAssertEqual(timeline.syncRequest, .fresh)

        let early = timeline.apply(try events("[\(append(1, "m:1", "x"))]", seq: 1))
        XCTAssertEqual(early.sync, .fresh)

        _ = timeline.apply(try snapshot(seq: 4))
        XCTAssertEqual(timeline.syncRequest, .resume(epoch: "e1", afterSeq: 4))
    }

    func testSnapshotReplacesItemsAndState() throws {
        var timeline = try loaded(seq: 3, items: "[\(agent("m:1", "old"))]")

        let result = timeline.apply(try snapshot(seq: 9, items: "[\(agent("m:2", "new"))]", epoch: "e2"))

        XCTAssertEqual(result.changes, [.items, .state])
        XCTAssertNil(result.sync)
        XCTAssertEqual(timeline.epoch, "e2")
        XCTAssertEqual(timeline.seq, 9)
        XCTAssertEqual(timeline.items.map(\.id), ["m:2"])
        XCTAssertEqual(timeline.state.status, "ready")
    }

    func testEventsUpsertAppendAndPatchState() throws {
        var timeline = try loaded(seq: 2, items: "[\(agent("m:1", "Hel"))]")
        let tool = #"{"id":"call-1","type":"tool","title":"Run tests","kind":"execute","status":"pending"}"#
        let done = #"{"id":"call-1","type":"tool","title":"Run tests","kind":"execute","status":"completed"}"#

        let result = timeline.apply(try events("""
            [\(append(3, "m:1", "lo")),
             {"type":"item","seq":4,"item":\(tool)},
             {"type":"item","seq":5,"item":\(done)},
             {"type":"state","seq":6,"patch":{"status":"running","cancelling":true}}]
            """, seq: 6))

        XCTAssertNil(result.sync)
        XCTAssertEqual(result.changes, [.items, .state])
        XCTAssertEqual(timeline.seq, 6)
        XCTAssertEqual(text(timeline, "m:1"), "Hello")
        XCTAssertEqual(timeline.items.map(\.id), ["m:1", "call-1"])
        guard case .tool(let call) = timeline.items[1].body else { return XCTFail("not a tool") }
        XCTAssertEqual(call.status, "completed")
        XCTAssertEqual(timeline.state.status, "running")
        XCTAssertTrue(timeline.state.cancelling)
    }

    func testDuplicatesAreSkipped() throws {
        var timeline = try loaded(seq: 2, items: "[\(agent("m:1", "a"))]")
        let frame = try events("[\(append(3, "m:1", "b"))]", seq: 3)

        _ = timeline.apply(frame)
        let again = timeline.apply(frame)

        XCTAssertEqual(again.changes, [])
        XCTAssertNil(again.sync)
        XCTAssertEqual(text(timeline, "m:1"), "ab")
    }

    func testAppendToAToolAndUnknownItemsOnlyAdvanceTheSeq() throws {
        let tool = #"{"id":"call-1","type":"tool","title":"Read","kind":"read","status":"pending"}"#
        var timeline = try loaded(seq: 1, items: "[\(tool)]")
        let before = timeline.items

        let result = timeline.apply(try events("""
            [\(append(2, "call-1", "x")),
             \(append(3, "missing", "x")),
             {"type":"item","seq":4,"item":{"id":"z","type":"hologram"}},
             {"type":"future","seq":5}]
            """, seq: 5))

        XCTAssertNil(result.sync)
        XCTAssertEqual(timeline.seq, 5)
        XCTAssertEqual(timeline.items, before)
    }

    func testRemoveDropsTheItemAndKeepsLaterEventsOfTheFrameOnTheRightItems() throws {
        var timeline = try loaded(seq: 1, items: "[\(agent("n:1", "Setting up")), \(agent("m:2", "a"))]")

        let result = timeline.apply(try events("""
            [{"type":"item","seq":2,"item":\(agent("n:3", "brief"))},
             {"type":"remove","seq":3,"id":"n:1"},
             {"type":"remove","seq":4,"id":"n:3"},
             \(append(5, "m:2", "b")),
             {"type":"item","seq":6,"item":\(agent("m:4", "c"))},
             \(append(7, "m:4", "d"))]
            """, seq: 7))

        XCTAssertNil(result.sync)
        XCTAssertEqual(result.changes, [.items])
        XCTAssertEqual(timeline.seq, 7)
        XCTAssertEqual(timeline.items.map(\.id), ["m:2", "m:4"])
        XCTAssertEqual(text(timeline, "m:2"), "ab")
        XCTAssertEqual(text(timeline, "m:4"), "cd")
    }

    func testRemoveOfAnUnknownItemOnlyAdvancesTheSeq() throws {
        var timeline = try loaded(seq: 1, items: "[\(agent("m:1", "a"))]")
        let before = timeline.items

        let result = timeline.apply(try events(#"[{"type":"remove","seq":2,"id":"missing"}, {"type":"remove","seq":3}]"#, seq: 3))

        XCTAssertNil(result.sync)
        XCTAssertEqual(result.changes, [])
        XCTAssertEqual(timeline.seq, 3)
        XCTAssertEqual(timeline.items, before)
    }

    func testGapAppliesWhatCameBeforeAndResumesFromThere() throws {
        var timeline = try loaded(seq: 1, items: "[\(agent("m:1", "a"))]")

        let result = timeline.apply(try events("[\(append(2, "m:1", "b")), \(append(4, "m:1", "d"))]", seq: 4))

        XCTAssertEqual(result.sync, .resume(epoch: "e1", afterSeq: 2))
        XCTAssertEqual(timeline.seq, 2)
        XCTAssertEqual(text(timeline, "m:1"), "ab")
    }

    func testFrameAheadOfItsEventsAsksForTheMissingTail() throws {
        var timeline = try loaded(seq: 3, items: "[]")

        let empty = timeline.apply(try events("[]", seq: 9))
        XCTAssertEqual(empty.sync, .resume(epoch: "e1", afterSeq: 3))

        let ack = timeline.apply(try events("[]", seq: 3))
        XCTAssertNil(ack.sync)
    }

    func testOtherEpochAsksForAFreshSnapshot() throws {
        var timeline = try loaded(seq: 4, items: "[\(agent("m:1"))]")

        let result = timeline.apply(try events("[\(append(5, "m:1", "x"))]", seq: 5, epoch: "e2"))

        XCTAssertEqual(result.sync, .fresh)
        XCTAssertEqual(timeline.seq, 4)
        XCTAssertEqual(text(timeline, "m:1"), "")
    }

    func testResultFramesChangeNothing() throws {
        var timeline = try loaded(seq: 1, items: "[]")

        let result = timeline.apply(try frame(#"{"t":"result","id":"c-1","ok":true}"#))

        XCTAssertEqual(result.changes, [])
        XCTAssertNil(result.sync)
    }

    func testCapDropsTheOldestItemsAndKeepsTheIndexRight() throws {
        var timeline = try loaded(
            seq: 1,
            items: "[\(agent("m:1", "1")), \(agent("m:2", "2")), \(agent("m:3", "3"))]",
            maxItems: 3
        )

        _ = timeline.apply(try events("[{\"type\":\"item\",\"seq\":2,\"item\":\(agent("m:4", "4"))}]", seq: 2))
        _ = timeline.apply(try events("[\(append(3, "m:3", "!")), \(append(4, "m:4", "?"))]", seq: 4))

        XCTAssertEqual(timeline.items.map(\.id), ["m:2", "m:3", "m:4"])
        XCTAssertEqual(text(timeline, "m:3"), "3!")
        XCTAssertEqual(text(timeline, "m:4"), "4?")
        XCTAssertTrue(timeline.truncated)
    }

    func testCapDropsFinishedStepsAfterTheLatestUserMessageBeforeOlderMessages() throws {
        var timeline = try loaded(
            seq: 1,
            items: "[\(user("u:0")), \(agent("m:1")), \(user("u:2")), \(tool("call-3", "completed")), \(thought("h:4")), \(agent("m:5"))]",
            maxItems: 4
        )

        _ = timeline.apply(try events("[\(append(2, "m:5", "!"))]", seq: 2))

        XCTAssertEqual(timeline.items.map(\.id), ["u:0", "m:1", "u:2", "m:5"])
        XCTAssertEqual(text(timeline, "m:5"), "!")
        XCTAssertFalse(timeline.truncated)
    }

    func testCapKeepsARunningToolInPlaceWhileOlderMessagesGo() throws {
        var timeline = try loaded(
            seq: 1,
            items: "[\(user("u:0")), \(agent("m:1")), \(tool("call-2", "in_progress"))]",
            maxItems: 3
        )

        _ = timeline.apply(try events(#"[{"type":"item","seq":2,"item":\#(agent("m:3", "a"))}]"#, seq: 2))
        _ = timeline.apply(try events(#"[{"type":"item","seq":3,"item":\#(tool("call-2", "completed"))}]"#, seq: 3))

        XCTAssertEqual(timeline.items.map(\.id), ["m:1", "call-2", "m:3"])
        guard case .tool(let call) = timeline.items[1].body else { return XCTFail("call-2 is not a tool") }
        XCTAssertEqual(call.status, "completed")
        XCTAssertTrue(timeline.truncated)
    }

    func testCapDropsFromTheFrontOnceTheFinishedStepsAreGone() throws {
        let timeline = try loaded(
            seq: 1,
            items: "[\(tool("call-0", "pending")), \(user("u:1")), \(thought("h:2")), \(agent("m:3"))]",
            maxItems: 1
        )

        XCTAssertEqual(timeline.items.map(\.id), ["m:3"])
        XCTAssertTrue(timeline.truncated)
    }

    func testCapDroppingOnlyStepsFromTheFrontLeavesTruncatedOff() throws {
        let timeline = try loaded(
            seq: 1,
            items: "[\(tool("call-0", "in_progress")), \(thought("h:1")), \(agent("m:2"))]",
            maxItems: 1
        )

        XCTAssertEqual(timeline.items.map(\.id), ["m:2"])
        XCTAssertFalse(timeline.truncated)
    }

    func testCapKeepsTheNewestItemEvenWhenItIsAFinishedStep() throws {
        let timeline = try loaded(seq: 1, items: "[\(agent("m:0")), \(thought("h:1"))]", maxItems: 1)

        XCTAssertEqual(timeline.items.map(\.id), ["h:1"])
        XCTAssertTrue(timeline.truncated)
    }
}
