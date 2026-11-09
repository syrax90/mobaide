import XCTest
@testable import Mobaide

private struct ChatFramesFixtureFile: Decodable {
    let commands: [CommandCase]
    let server: [ServerCase]

    struct CommandCase: Decodable {
        let name: String
        let command: JSONValue
    }

    struct ServerCase: Decodable {
        let name: String
        let frame: JSONValue
    }
}

/// The iOS arm of `frames/chat_frames.json`: every golden command rebuilt
/// through the `ChatCommand` builders, and every golden agent frame decoded by
/// `ChatServerFrame.decode` and applied to a `ChatTimeline`, as the web and
/// Android clients do with the same file.
final class ChatFrameFixtureTests: XCTestCase {
    private let epoch = "9f3a1c2b7d4e"

    private func loadFile() throws -> ChatFramesFixtureFile {
        try FixtureLoader.load(ChatFramesFixtureFile.self, path: "frames/chat_frames.json")
    }

    private func builders() throws -> [String: (String) -> ChatCommand] {
        let fresh = ChatTimeline().syncRequest
        let resume = try loaded().syncRequest
        return [
            "sync_fresh": { .sync(id: $0, epoch: fresh.epoch, afterSeq: fresh.afterSeq) },
            "sync_resume": { .sync(id: $0, epoch: resume.epoch, afterSeq: resume.afterSeq) },
            "prompt_text": { .prompt(id: $0, text: "Fix the failing test", images: []) },
            "prompt_with_image": {
                .prompt(
                    id: $0,
                    text: "What is on this screen?",
                    images: [ChatImagePayload(mime: "image/png", data: "iVBORw0KGgo=")]
                )
            },
            "cancel": { .cancel(id: $0) },
            "set_config_select": { .setConfig(id: $0, configId: "model", value: ChatConfigValue.string("sonnet").jsonValue) },
            "set_config_boolean": { .setConfig(id: $0, configId: "fast", value: ChatConfigValue.bool(true).jsonValue) },
            "respond_permission": { .respondPermission(id: $0, requestId: "r:9", optionId: "allow") },
            "respond_form_accept": {
                .respondForm(
                    id: $0,
                    requestId: "r:12",
                    action: "accept",
                    values: ["framework": .string("vue"), "extras": .array([.string("lint")]), "strict": .bool(false)]
                )
            },
            "respond_form_decline": { .respondForm(id: $0, requestId: "r:12", action: "decline", values: nil) },
            "dequeue": { .dequeue(id: $0, queueId: "q:14") },
            "restart": { .restart(id: $0) },
            "steer": { .steer(id: $0, queueId: "q:14") },
        ]
    }

    private func server(_ name: String) throws -> ChatServerFrame {
        let entry = try XCTUnwrap(try loadFile().server.first { $0.name == name }, "no server case \(name)")
        let payload = try WireJSON.encoder().encode(entry.frame)
        return try XCTUnwrap(ChatServerFrame.decode(payload), "\(name) did not decode")
    }

    private func snapshot() throws -> ChatSnapshotFrame {
        guard case .snapshot(let snapshot) = try server("snapshot_full") else {
            throw FixtureLoaderError.caseNotFound("snapshot_full is not a snapshot")
        }
        return snapshot
    }

    private func batch() throws -> ChatEventsFrame {
        guard case .events(let events) = try server("events_batch") else {
            throw FixtureLoaderError.caseNotFound("events_batch is not an events frame")
        }
        return events
    }

    private func loaded() throws -> ChatTimeline {
        var timeline = ChatTimeline()
        _ = timeline.apply(.snapshot(try snapshot()))
        return timeline
    }

    private func itemKind(_ item: ChatItem) -> String {
        switch item.body {
        case .user: return "user"
        case .agent: return "agent"
        case .thought: return "thought"
        case .tool: return "tool"
        case .notice: return "notice"
        case .compaction: return "compaction"
        case .turnEnd: return "turn_end"
        case .error: return "error"
        }
    }

    private func requestKind(_ request: ChatPendingRequest) -> String {
        switch request {
        case .permission: return "permission"
        case .form: return "form"
        case .other: return "other"
        }
    }

    func testEveryGoldenCommandHasABuilder() throws {
        let file = try loadFile()
        XCTAssertEqual(file.commands.map(\.name).sorted(), try builders().keys.sorted())
    }

    func testEveryGoldenCommandIsEncodedByTheBuilders() throws {
        let file = try loadFile()
        let builders = try builders()
        XCTAssertFalse(file.commands.isEmpty)
        for commandCase in file.commands {
            guard case .object(let fields) = commandCase.command, case .string(let id) = fields["id"] else {
                return XCTFail("\(commandCase.name): command has no id")
            }
            let build = try XCTUnwrap(builders[commandCase.name], "no builder for \(commandCase.name)")
            let bytes = build(id).encoded

            XCTAssertEqual(bytes.first, ChatFrameType.command, commandCase.name)
            let tree = try WireJSON.decoder().decode(JSONValue.self, from: Data(bytes.dropFirst()))
            XCTAssertEqual(tree, commandCase.command, commandCase.name)
        }
    }

    func testEveryServerFrameDecodes() throws {
        let file = try loadFile()
        XCTAssertFalse(file.server.isEmpty)
        for serverCase in file.server {
            let payload = try WireJSON.encoder().encode(serverCase.frame)
            XCTAssertNotNil(ChatServerFrame.decode(payload), serverCase.name)
        }
    }

    func testSnapshotDecodesItemsAndState() throws {
        let frame = try snapshot()

        XCTAssertEqual(frame.epoch, epoch)
        XCTAssertEqual(frame.seq, 41)
        XCTAssertFalse(frame.truncated)
        XCTAssertEqual(
            frame.items.map(itemKind),
            ["user", "thought", "agent", "tool", "tool", "notice", "compaction", "turn_end", "error"]
        )
        guard case .user(_, let attachments) = frame.items[0].body else { return XCTFail("not a user item") }
        XCTAssertEqual(attachments.map(\.kind), ["image"])
        guard case .tool(let edit) = frame.items[3].body else { return XCTFail("not a tool item") }
        XCTAssertEqual(edit.locations.map(\.label), ["/home/user/proj/login.py:12"])
        XCTAssertEqual(edit.content, [.diff(ChatDiff(
            path: "/home/user/proj/login.py",
            newFile: false,
            diff: "@@ -1,2 +1,2 @@\n-if user:\n+if user is not None:\n     login(user)",
            added: 1,
            removed: 1,
            truncated: false
        ))])
        XCTAssertEqual(frame.items[7].body, .turnEnd(stopReason: "cancelled", text: "Stopped"))

        let state = frame.state
        XCTAssertEqual(state.status, "running")
        XCTAssertEqual(state.agent?.title, "Claude Agent")
        XCTAssertEqual(state.config.map(\.id), ["mode", "model", "fast"])
        XCTAssertEqual(state.config.map(\.type), ["select", "select", "boolean"])
        XCTAssertEqual(state.config.map(\.value), [.string("default"), .string("opus"), .bool(false)])
        XCTAssertEqual(state.commands.map(\.name), ["review", "compact"])
        XCTAssertEqual(state.usage?.used, 53_000)
        XCTAssertEqual(state.usage?.size, 200_000)
        XCTAssertEqual(state.usage?.costAmount, 0.045)
        XCTAssertEqual(state.usage?.costCurrency, "USD")
        XCTAssertEqual(state.plan.map(\.status), ["completed", "in_progress", "pending"])
        XCTAssertEqual(state.requests.map(requestKind), ["permission", "form"])
        guard case .permission(let permission) = state.requests[0] else { return XCTFail("not a permission") }
        XCTAssertEqual(permission.toolCallId, "toolu_02")
        XCTAssertEqual(permission.options.map(\.id), ["allow", "always", "reject"])
        guard case .form(let form) = state.requests[1] else { return XCTFail("not a form") }
        XCTAssertNil(form.toolCallId)
        XCTAssertEqual(form.fields.map(\.type), ["select", "multiselect", "text", "boolean", "integer"])
        XCTAssertEqual(state.queue.map(\.id), ["q:14"])
        XCTAssertEqual(state.queue.map(\.text), ["Also update the README"])
        XCTAssertTrue(state.promptImages)
        XCTAssertTrue(state.steering)
    }

    func testEventsBatchAppliesOnTopOfTheSnapshot() throws {
        var timeline = try loaded()
        let itemsInSnapshot = timeline.items.count

        let result = timeline.apply(.events(try batch()))

        XCTAssertNil(result.sync)
        XCTAssertEqual(result.changes, [.items, .state])
        XCTAssertEqual(timeline.seq, 45)
        XCTAssertEqual(timeline.items.count, itemsInSnapshot + 2)
        XCTAssertEqual(timeline.items.first { $0.id == "m:8" }?.body, .agent(text: "Done: the tests pass."))
        guard case .tool(let running) = timeline.items.last?.body else { return XCTFail("not a tool item") }
        XCTAssertEqual(running.status, "in_progress")
        XCTAssertEqual(timeline.state.status, "ready")
        XCTAssertNil(timeline.state.turnStartedAt)
        XCTAssertTrue(timeline.state.requests.isEmpty)
        XCTAssertEqual(timeline.state.queue.count, 1)

        let items = timeline.items
        let ack = timeline.apply(try server("events_empty_ack"))

        XCTAssertEqual(ack.changes, [])
        XCTAssertNil(ack.sync)
        XCTAssertEqual(timeline.items, items)
    }

    func testSyncRulesOnTheGoldenFrames() throws {
        let events = try batch()
        XCTAssertEqual(try loaded().syncRequest, .resume(epoch: epoch, afterSeq: 41))

        var replayed = try loaded()
        _ = replayed.apply(.events(events))
        let again = replayed.apply(.events(events))
        XCTAssertEqual(again.changes, [])
        XCTAssertNil(again.sync)
        XCTAssertEqual(replayed.seq, 45)

        var gapped = try loaded()
        let gap = gapped.apply(.events(ChatEventsFrame(epoch: events.epoch, seq: events.seq, events: Array(events.events.dropFirst()))))
        XCTAssertEqual(gap.sync, .resume(epoch: epoch, afterSeq: 41))
        XCTAssertEqual(gapped.seq, 41)

        var otherEpoch = try loaded()
        let before = otherEpoch.items
        let other = otherEpoch.apply(.events(ChatEventsFrame(epoch: "other", seq: events.seq, events: events.events)))
        XCTAssertEqual(other.sync, .fresh)
        XCTAssertEqual(otherEpoch.items, before)
    }

    func testResultsDecode() throws {
        guard case .result(let ok) = try server("result_ok") else { return XCTFail("result_ok is not a result") }
        XCTAssertEqual(ok, ChatResultFrame(
            id: "c-3",
            ok: true,
            data: .object(["queued": .bool(true), "queue_id": .string("q:14")]),
            error: nil
        ))

        guard case .result(let refused) = try server("result_error") else { return XCTFail("result_error is not a result") }
        XCTAssertEqual(refused, ChatResultFrame(id: "c-8", ok: false, data: nil, error: "This request was already answered"))
    }
}
