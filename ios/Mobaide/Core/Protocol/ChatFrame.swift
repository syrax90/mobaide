import Foundation

/// Chat sessions share the terminal socket and its one-type-byte framing:
/// `c` carries one JSON command up, `a` one JSON frame down. The `s` state
/// frame keeps its terminal meaning; `b`/`o`/`e` are for older clients and are
/// ignored by the chat screen.
enum ChatFrameType {
    static let command: UInt8 = 0x63
    static let agent: UInt8 = 0x61
}

struct ChatImagePayload: Encodable, Equatable, Sendable {
    let mime: String
    let data: String
}

/// One client command. Every optional field is omitted from the JSON when nil.
struct ChatCommand: Encodable, Equatable, Sendable {
    let id: String
    let op: String
    var epoch: String?
    var after_seq: Int?
    var text: String?
    var images: [ChatImagePayload]?
    var config_id: String?
    var value: JSONValue?
    var request_id: String?
    var option_id: String?
    var action: String?
    var values: [String: JSONValue]?
    var queue_id: String?

    var encoded: Data {
        let body = (try? WireJSON.encoder().encode(self)) ?? Data("{}".utf8)
        return Data([ChatFrameType.command]) + body
    }

    static func sync(id: String, epoch: String?, afterSeq: Int?) -> ChatCommand {
        ChatCommand(id: id, op: "sync", epoch: epoch, after_seq: afterSeq)
    }

    static func prompt(id: String, text: String, images: [ChatImagePayload]) -> ChatCommand {
        ChatCommand(id: id, op: "prompt", text: text, images: images.isEmpty ? nil : images)
    }

    static func cancel(id: String) -> ChatCommand {
        ChatCommand(id: id, op: "cancel")
    }

    static func setConfig(id: String, configId: String, value: JSONValue) -> ChatCommand {
        ChatCommand(id: id, op: "set_config", config_id: configId, value: value)
    }

    static func respondPermission(id: String, requestId: String, optionId: String) -> ChatCommand {
        ChatCommand(id: id, op: "respond", request_id: requestId, option_id: optionId)
    }

    static func respondForm(id: String, requestId: String, action: String, values: [String: JSONValue]?) -> ChatCommand {
        ChatCommand(id: id, op: "respond", request_id: requestId, action: action, values: values)
    }

    static func dequeue(id: String, queueId: String) -> ChatCommand {
        ChatCommand(id: id, op: "dequeue", queue_id: queueId)
    }

    /// Sends a queued message into the running turn now.
    static func steer(id: String, queueId: String) -> ChatCommand {
        ChatCommand(id: id, op: "steer", queue_id: queueId)
    }

    static func restart(id: String) -> ChatCommand {
        ChatCommand(id: id, op: "restart")
    }
}

struct ChatSnapshotFrame: Sendable {
    let epoch: String
    let seq: Int
    let truncated: Bool
    let state: ChatState
    let items: [ChatItem]
}

enum ChatEvent: Sendable, Decodable {
    /// `item` is nil for an item type this client does not know.
    case item(seq: Int, item: ChatItem?)
    case append(seq: Int, id: String, text: String)
    case state(seq: Int, patch: ChatStatePatch)
    case other(seq: Int)

    var seq: Int {
        switch self {
        case .item(let seq, _), .append(let seq, _, _), .state(let seq, _), .other(let seq): return seq
        }
    }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let seq = c.int("seq") else { throw ChatSkipError() }
        switch c.string("type") {
        case "item":
            self = .item(seq: seq, item: c.value(ChatItem.self, "item"))
        case "append":
            guard let id = c.string("id") else {
                self = .other(seq: seq)
                return
            }
            self = .append(seq: seq, id: id, text: c.string("text") ?? "")
        case "state":
            if let patch = c.value(ChatStatePatch.self, "patch") {
                self = .state(seq: seq, patch: patch)
            } else {
                self = .other(seq: seq)
            }
        default:
            self = .other(seq: seq)
        }
    }
}

struct ChatEventsFrame: Sendable {
    let epoch: String
    let seq: Int
    let events: [ChatEvent]
}

struct ChatResultFrame: Sendable, Equatable {
    let id: String?
    let ok: Bool
    let data: JSONValue?
    let error: String?
}

enum ChatServerFrame: Sendable {
    case snapshot(ChatSnapshotFrame)
    case events(ChatEventsFrame)
    case result(ChatResultFrame)

    /// Parses one `a` payload (the bytes after the type byte); nil for a frame
    /// this client cannot read.
    static func decode(_ payload: Data) -> ChatServerFrame? {
        try? WireJSON.decoder().decode(Envelope.self, from: payload).frame
    }

    private struct Envelope: Decodable {
        let frame: ChatServerFrame?

        init(from decoder: any Decoder) throws {
            let c = try decoder.container(keyedBy: ChatKey.self)
            switch c.string("t") {
            case "snapshot":
                guard let epoch = c.string("epoch"), let seq = c.int("seq") else {
                    frame = nil
                    return
                }
                var state = ChatState()
                if let patch = c.value(ChatStatePatch.self, "state") {
                    state.apply(patch)
                }
                frame = .snapshot(ChatSnapshotFrame(
                    epoch: epoch,
                    seq: seq,
                    truncated: c.bool("truncated") ?? false,
                    state: state,
                    items: c.list(ChatItem.self, "items")
                ))
            case "events":
                guard let epoch = c.string("epoch"), let seq = c.int("seq") else {
                    frame = nil
                    return
                }
                frame = .events(ChatEventsFrame(epoch: epoch, seq: seq, events: c.list(ChatEvent.self, "events")))
            case "result":
                frame = .result(ChatResultFrame(
                    id: c.string("id"),
                    ok: c.bool("ok") ?? false,
                    data: c.value(JSONValue.self, "data"),
                    error: c.string("error")
                ))
            default:
                frame = nil
            }
        }
    }
}
