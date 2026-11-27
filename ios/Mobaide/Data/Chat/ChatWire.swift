import Foundation

/// Chat-session wire model (PROTOCOL.md, "chat sessions"). Decoding is lenient
/// everywhere: an unknown item type, content part or request kind is skipped or
/// degraded instead of failing the whole frame, so a newer agent never blanks
/// an older client's timeline.

struct ChatKey: CodingKey, ExpressibleByStringLiteral {
    let stringValue: String
    var intValue: Int? { nil }

    init(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { nil }
    init(stringLiteral value: String) { stringValue = value }
}

struct ChatSkipError: Error {}

/// Decodes every element it can and drops the rest.
struct LossyList<Element: Decodable>: Decodable {
    let elements: [Element]

    init(from decoder: any Decoder) throws {
        var container = try decoder.unkeyedContainer()
        var elements: [Element] = []
        while !container.isAtEnd {
            if let element = try? container.decode(Element.self) {
                elements.append(element)
            } else {
                _ = try? container.decode(SkippedValue.self)
            }
        }
        self.elements = elements
    }

    private struct SkippedValue: Decodable {
        init(from decoder: any Decoder) throws {}
    }
}

extension KeyedDecodingContainer where Key == ChatKey {
    func string(_ key: String) -> String? { try? decodeIfPresent(String.self, forKey: ChatKey(stringValue: key)) }
    func bool(_ key: String) -> Bool? { try? decodeIfPresent(Bool.self, forKey: ChatKey(stringValue: key)) }
    func int(_ key: String) -> Int? { try? decodeIfPresent(Int.self, forKey: ChatKey(stringValue: key)) }
    func double(_ key: String) -> Double? { try? decodeIfPresent(Double.self, forKey: ChatKey(stringValue: key)) }
    func has(_ key: String) -> Bool { contains(ChatKey(stringValue: key)) }

    func value<T: Decodable>(_ type: T.Type, _ key: String) -> T? {
        try? decodeIfPresent(T.self, forKey: ChatKey(stringValue: key))
    }

    func list<T: Decodable>(_ type: T.Type, _ key: String) -> [T] {
        value(LossyList<T>.self, key)?.elements ?? []
    }
}

// MARK: - Content

struct ChatDiff: Equatable, Sendable {
    let path: String
    let newFile: Bool
    let diff: String
    let added: Int
    let removed: Int
    let truncated: Bool
}

enum ChatContentPart: Equatable, Sendable, Decodable {
    case text(String, truncated: Bool)
    case diff(ChatDiff)
    case image(mime: String, data: String?)
    case link(uri: String, name: String?)
    case resource(uri: String, name: String?, text: String?)
    case terminal(id: String)

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        switch c.string("type") {
        case "text":
            self = .text(c.string("text") ?? "", truncated: c.bool("truncated") ?? false)
        case "diff":
            self = .diff(ChatDiff(
                path: c.string("path") ?? "",
                newFile: c.bool("new_file") ?? false,
                diff: c.string("diff") ?? "",
                added: c.int("added") ?? 0,
                removed: c.int("removed") ?? 0,
                truncated: c.bool("truncated") ?? false
            ))
        case "image":
            self = .image(mime: c.string("mime") ?? "image/png", data: c.string("data"))
        case "link":
            self = .link(uri: c.string("uri") ?? "", name: c.string("name"))
        case "resource":
            self = .resource(uri: c.string("uri") ?? "", name: c.string("name"), text: c.string("text"))
        case "terminal":
            self = .terminal(id: c.string("terminal_id") ?? "")
        default:
            throw ChatSkipError()
        }
    }
}

// MARK: - Items

struct ChatAttachment: Equatable, Sendable, Decodable {
    let kind: String
    let mime: String?
    let name: String?

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        kind = c.string("kind") ?? "file"
        mime = c.string("mime")
        name = c.string("name")
    }
}

struct ChatLocation: Equatable, Sendable, Decodable {
    let path: String
    let line: Int?

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let path = c.string("path") else { throw ChatSkipError() }
        self.path = path
        line = c.int("line")
    }

    var label: String { line.map { "\(path):\($0)" } ?? path }
}

struct ChatToolCall: Equatable, Sendable {
    var title: String
    var kind: String
    var status: String
    var subtitle: String?
    var locations: [ChatLocation]
    var content: [ChatContentPart]
    var input: String?
    var subagentId: String? = nil

    var diffStats: (added: Int, removed: Int)? {
        guard ["edit", "delete", "move"].contains(kind) else { return nil }
        let diffs = content.compactMap { part -> ChatDiff? in
            if case .diff(let diff) = part { return diff }
            return nil
        }
        guard !diffs.isEmpty else { return nil }
        return (diffs.reduce(0) { $0 + $1.added }, diffs.reduce(0) { $0 + $1.removed })
    }

    var firstTextLine: String? {
        for part in content {
            if case .text(let text, _) = part {
                return text.split(whereSeparator: \.isNewline)
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                    .first { !$0.isEmpty && !$0.hasPrefix("```") && !$0.hasPrefix("~~~") }
            }
        }
        return nil
    }
}

/// A subagent the agent runs as a child session. An unknown `state` reads as `unknown`.
struct ChatSubagent: Equatable, Sendable {
    static let states: Set<String> = ["running", "requires_action", "idle", "unknown"]

    var subagentId: String
    var parentSubagentId: String?
    var title: String
    var description: String?
    var task: String?
    var state: String
    var stopReason: String?
    var result: String?
    var canCancel: Bool
    var plan: [ChatPlanEntry]

    var isRunning: Bool { state == "running" }
}

struct ChatItem: Identifiable, Equatable, Sendable, Decodable {
    enum Body: Equatable, Sendable {
        case user(text: String, attachments: [ChatAttachment])
        case agent(text: String)
        case thought(text: String)
        case tool(ChatToolCall)
        case notice(severity: String, title: String, text: String)
        case compaction(status: String, text: String)
        case turnEnd(stopReason: String?, text: String)
        case error(text: String)
        case subagent(ChatSubagent)
    }

    let id: String
    var body: Body
    /// An agent message that is the final answer of a finished turn: `fork`
    /// can keep the chat up to it.
    var forkPoint = false

    init(id: String, body: Body, forkPoint: Bool = false) {
        self.id = id
        self.body = body
        self.forkPoint = forkPoint
    }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let id = c.string("id") else { throw ChatSkipError() }
        self.id = id
        let text = c.string("text") ?? ""
        switch c.string("type") {
        case "user":
            body = .user(text: text, attachments: c.list(ChatAttachment.self, "attachments"))
        case "agent":
            body = .agent(text: text)
            forkPoint = c.bool("fork_point") ?? false
        case "thought":
            body = .thought(text: text)
        case "tool":
            body = .tool(ChatToolCall(
                title: c.string("title") ?? "",
                kind: c.string("kind") ?? "other",
                status: c.string("status") ?? "pending",
                subtitle: c.string("subtitle"),
                locations: c.list(ChatLocation.self, "locations"),
                content: c.list(ChatContentPart.self, "content"),
                input: c.string("input"),
                subagentId: c.string("subagent_id")
            ))
        case "notice":
            body = .notice(severity: c.string("severity") ?? "info", title: c.string("title") ?? "", text: text)
        case "compaction":
            body = .compaction(status: c.string("status") ?? "completed", text: text)
        case "turn_end":
            body = .turnEnd(stopReason: c.string("stop_reason"), text: text)
        case "error":
            body = .error(text: text)
        case "subagent":
            let subagentId = c.string("subagent_id") ?? (id.hasPrefix("sa:") ? String(id.dropFirst(3)) : id)
            let title = c.string("title") ?? ""
            let state = c.string("state") ?? "unknown"
            body = .subagent(ChatSubagent(
                subagentId: subagentId,
                parentSubagentId: c.string("parent_subagent_id"),
                title: title.isEmpty ? "Subagent" : title,
                description: c.string("description"),
                task: c.string("task"),
                state: ChatSubagent.states.contains(state) ? state : "unknown",
                stopReason: c.string("stop_reason"),
                result: c.string("result"),
                canCancel: c.bool("can_cancel") ?? false,
                plan: c.list(ChatPlanEntry.self, "plan")
            ))
        default:
            throw ChatSkipError()
        }
    }

    mutating func appendText(_ suffix: String) {
        switch body {
        case .user(let text, let attachments): body = .user(text: text + suffix, attachments: attachments)
        case .agent(let text): body = .agent(text: text + suffix)
        case .thought(let text): body = .thought(text: text + suffix)
        case .notice(let severity, let title, let text): body = .notice(severity: severity, title: title, text: text + suffix)
        case .compaction(let status, let text): body = .compaction(status: status, text: text + suffix)
        case .turnEnd(let reason, let text): body = .turnEnd(stopReason: reason, text: text + suffix)
        case .error(let text): body = .error(text: text + suffix)
        case .tool, .subagent: break
        }
    }
}

// MARK: - State

struct ChatAgentInfo: Equatable, Sendable, Decodable {
    let name: String?
    let title: String?
    let version: String?

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        name = c.string("name")
        title = c.string("title")
        version = c.string("version")
    }

    var displayName: String? { title ?? name }
}

enum ChatConfigValue: Equatable, Sendable, Decodable {
    case string(String)
    case bool(Bool)
    case other

    init(from decoder: any Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let flag = try? c.decode(Bool.self) {
            self = .bool(flag)
        } else if let text = try? c.decode(String.self) {
            self = .string(text)
        } else {
            self = .other
        }
    }

    var jsonValue: JSONValue {
        switch self {
        case .string(let value): return .string(value)
        case .bool(let value): return .bool(value)
        case .other: return .null
        }
    }
}

struct ChatConfigChoice: Equatable, Sendable, Decodable, Identifiable {
    let value: String
    let name: String
    let description: String?
    let group: String?

    var id: String { value }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let value = c.string("value") else { throw ChatSkipError() }
        self.value = value
        name = c.string("name") ?? value
        description = c.string("description")
        group = c.string("group")
    }
}

struct ChatConfigOption: Equatable, Sendable, Decodable, Identifiable {
    let id: String
    let name: String
    let description: String?
    let category: String?
    let type: String
    var value: ChatConfigValue
    let options: [ChatConfigChoice]

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let id = c.string("id") else { throw ChatSkipError() }
        self.id = id
        name = c.string("name") ?? id
        description = c.string("description")
        category = c.string("category")
        type = c.string("type") ?? ""
        value = c.value(ChatConfigValue.self, "value") ?? .other
        options = c.list(ChatConfigChoice.self, "options")
    }

    var isKnownType: Bool { type == "select" || type == "boolean" }

    var currentChoice: ChatConfigChoice? {
        guard case .string(let current) = value else { return nil }
        return options.first { $0.value == current }
    }
}

struct ChatSlashCommand: Equatable, Sendable, Decodable, Identifiable {
    let name: String
    let description: String?
    let hint: String?

    var id: String { name }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let name = c.string("name"), !name.isEmpty else { throw ChatSkipError() }
        self.name = name
        description = c.string("description")
        hint = c.string("hint")
    }
}

struct ChatUsage: Equatable, Sendable, Decodable {
    let used: Double
    let size: Double
    let costAmount: Double?
    let costCurrency: String?

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        used = c.double("used") ?? 0
        size = c.double("size") ?? 0
        if let cost = try? c.nestedContainer(keyedBy: ChatKey.self, forKey: "cost") {
            costAmount = cost.double("amount")
            costCurrency = cost.string("currency")
        } else {
            costAmount = nil
            costCurrency = nil
        }
    }

    var fraction: Double? {
        guard size > 0 else { return nil }
        return min(max(used / size, 0), 1)
    }
}

struct ChatPlanEntry: Equatable, Sendable, Decodable {
    let content: String
    let status: String
    let priority: String?

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        content = c.string("content") ?? ""
        status = c.string("status") ?? "pending"
        priority = c.string("priority")
    }
}

struct ChatQueuedMessage: Equatable, Sendable, Decodable, Identifiable {
    let id: String
    let text: String
    let images: Int

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let id = c.string("id") else { throw ChatSkipError() }
        self.id = id
        text = c.string("text") ?? ""
        images = c.int("images") ?? 0
    }
}

/// An automatic continue that waits in the queue (as `queueId`) for a usage limit to reset.
struct ChatLimitWait: Equatable, Sendable, Decodable {
    let queueId: String
    let resetsAt: String?
    let continuesAt: String
    let limitType: String?

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let queueId = c.string("queue_id"), let continuesAt = c.string("continues_at") else {
            throw ChatSkipError()
        }
        self.queueId = queueId
        self.continuesAt = continuesAt
        resetsAt = c.string("resets_at")
        limitType = c.string("limit_type")
    }
}

struct ChatPermissionOption: Equatable, Sendable, Decodable, Identifiable {
    let id: String
    let name: String
    let kind: String

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let id = c.string("id") else { throw ChatSkipError() }
        self.id = id
        name = c.string("name") ?? id
        kind = c.string("kind") ?? "allow_once"
    }

    var isReject: Bool { kind.hasPrefix("reject") }
}

struct ChatFormChoice: Equatable, Sendable, Decodable, Identifiable {
    let value: String
    let label: String
    let description: String?

    var id: String { value }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let value = c.string("value") else { throw ChatSkipError() }
        self.value = value
        label = c.string("label") ?? value
        description = c.string("description")
    }
}

struct ChatFormField: Equatable, Sendable, Decodable, Identifiable {
    let key: String
    let title: String
    let description: String?
    let required: Bool
    let type: String
    let options: [ChatFormChoice]
    let defaultValue: JSONValue?

    var id: String { key }

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let key = c.string("key") else { throw ChatSkipError() }
        self.key = key
        title = c.string("title") ?? key
        description = c.string("description")
        required = c.bool("required") ?? false
        type = c.string("type") ?? "text"
        options = c.list(ChatFormChoice.self, "options")
        defaultValue = c.value(JSONValue.self, "default")
    }
}

enum ChatPendingRequest: Equatable, Sendable, Decodable, Identifiable {
    struct Permission: Equatable, Sendable {
        let id: String
        let toolCallId: String?
        let title: String
        let toolKind: String
        let subtitle: String?
        let content: [ChatContentPart]
        let options: [ChatPermissionOption]
    }

    struct Form: Equatable, Sendable {
        let id: String
        let toolCallId: String?
        let title: String
        let fields: [ChatFormField]
    }

    /// Sign-in to the coding agent on the PC. `choose` offers `methods`;
    /// `waiting` runs one: open `url`, enter `userCode` there, or paste the
    /// code the page shows into the `input` field.
    struct SignIn: Equatable, Sendable {
        struct Method: Equatable, Sendable, Decodable {
            let id: String
            let name: String
            let description: String

            init(id: String, name: String, description: String) {
                self.id = id
                self.name = name
                self.description = description
            }

            init(from decoder: any Decoder) throws {
                let c = try decoder.container(keyedBy: ChatKey.self)
                guard let id = c.string("id") else { throw ChatSkipError() }
                self.init(id: id, name: c.string("name") ?? id, description: c.string("description") ?? "")
            }
        }

        struct Input: Equatable, Sendable {
            let label: String
            let secret: Bool
        }

        let id: String
        let toolCallId: String?
        let title: String
        let text: String
        let stage: String
        let methods: [Method]
        let url: URL?
        let userCode: String?
        let input: Input?
        let busy: Bool
        let error: String?

        var waiting: Bool { stage == "waiting" }
    }

    case permission(Permission)
    case form(Form)
    case signIn(SignIn)
    case other(id: String, toolCallId: String?, title: String)

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        guard let id = c.string("id") else { throw ChatSkipError() }
        let toolCallId = c.string("tool_call_id")
        let title = c.string("title") ?? ""
        switch c.string("kind") {
        case "permission":
            self = .permission(Permission(
                id: id,
                toolCallId: toolCallId,
                title: title,
                toolKind: c.string("tool_kind") ?? "other",
                subtitle: c.string("subtitle"),
                content: c.list(ChatContentPart.self, "content"),
                options: c.list(ChatPermissionOption.self, "options")
            ))
        case "form":
            self = .form(Form(id: id, toolCallId: toolCallId, title: title, fields: c.list(ChatFormField.self, "fields")))
        case "sign_in":
            self = .signIn(Self.signIn(c, id: id, toolCallId: toolCallId, title: title))
        default:
            self = .other(id: id, toolCallId: toolCallId, title: title)
        }
    }

    private static func signIn(
        _ c: KeyedDecodingContainer<ChatKey>, id: String, toolCallId: String?, title: String
    ) -> SignIn {
        var input: SignIn.Input?
        if let field = try? c.nestedContainer(keyedBy: ChatKey.self, forKey: "input") {
            input = SignIn.Input(label: field.string("label") ?? "", secret: field.bool("secret") ?? false)
        }
        let url = c.string("url").flatMap { $0.hasPrefix("https://") ? URL(string: $0) : nil }
        return SignIn(
            id: id,
            toolCallId: toolCallId,
            title: title,
            text: c.string("text") ?? "",
            stage: c.string("stage") ?? "choose",
            methods: c.list(SignIn.Method.self, "methods"),
            url: url,
            userCode: c.string("user_code"),
            input: input,
            busy: c.bool("busy") ?? false,
            error: c.string("error")
        )
    }

    var id: String {
        switch self {
        case .permission(let request): return request.id
        case .form(let request): return request.id
        case .signIn(let request): return request.id
        case .other(let id, _, _): return id
        }
    }

    var toolCallId: String? {
        switch self {
        case .permission(let request): return request.toolCallId
        case .form(let request): return request.toolCallId
        case .signIn(let request): return request.toolCallId
        case .other(_, let toolCallId, _): return toolCallId
        }
    }
}

/// One `prompt_suggestions` entry.
struct ChatPromptSuggestion: Sendable, Decodable {
    let text: String
}

/// The `fork` state key: the agent forks the whole chat, and at an answer
/// marked `fork_point`. An older agent leaves the key out.
struct ChatForkSupport: Equatable, Sendable, Decodable {
    var chat = false
    var atAnswer = false

    init() {}

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        chat = c.bool("chat") ?? false
        atAnswer = c.bool("at_answer") ?? false
    }
}

/// A shallow state patch: a nil field is absent; for nullable fields an inner
/// nil is an explicit `null`.
struct ChatStatePatch: Sendable, Decodable {
    var status: String?
    var error: String??
    var agent: ChatAgentInfo??
    var title: String??
    var cwd: String??
    var acpSessionId: String??
    var config: [ChatConfigOption]?
    var commands: [ChatSlashCommand]?
    var usage: ChatUsage??
    var plan: [ChatPlanEntry]?
    var requests: [ChatPendingRequest]?
    var queue: [ChatQueuedMessage]?
    var promptImages: Bool?
    var turnStartedAt: String??
    var cancelling: Bool?
    var steering: Bool?
    var background: ChatBackgroundWork??
    var limitWait: ChatLimitWait??
    var newConversation: Bool?
    var promptSuggestions: [String]?
    var fork: ChatForkSupport?

    init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: ChatKey.self)
        func nullable<T: Decodable>(_ type: T.Type, _ key: String) -> T?? {
            c.has(key) ? .some(c.value(T.self, key)) : .none
        }
        func listIfPresent<T: Decodable>(_ type: T.Type, _ key: String) -> [T]? {
            c.has(key) ? c.list(T.self, key) : nil
        }
        if c.has("status") { status = c.string("status") ?? "ready" }
        error = nullable(String.self, "error")
        agent = nullable(ChatAgentInfo.self, "agent")
        title = nullable(String.self, "title")
        cwd = nullable(String.self, "cwd")
        acpSessionId = nullable(String.self, "acp_session_id")
        config = listIfPresent(ChatConfigOption.self, "config")
        commands = listIfPresent(ChatSlashCommand.self, "commands")
        usage = nullable(ChatUsage.self, "usage")
        plan = listIfPresent(ChatPlanEntry.self, "plan")
        requests = listIfPresent(ChatPendingRequest.self, "requests")
        queue = listIfPresent(ChatQueuedMessage.self, "queue")
        if c.has("prompt_caps") {
            let caps = try? c.nestedContainer(keyedBy: ChatKey.self, forKey: "prompt_caps")
            promptImages = caps?.bool("image") ?? false
        }
        turnStartedAt = nullable(String.self, "turn_started_at")
        if c.has("cancelling") { cancelling = c.bool("cancelling") ?? false }
        if c.has("steering") { steering = c.bool("steering") ?? false }
        if c.has("background") {
            background = .some(c.string("background").flatMap(ChatBackgroundWork.init(rawValue:)))
        }
        limitWait = nullable(ChatLimitWait.self, "limit_wait")
        if c.has("new_conversation") { newConversation = c.bool("new_conversation") ?? false }
        if c.has("prompt_suggestions") {
            promptSuggestions = c.list(ChatPromptSuggestion.self, "prompt_suggestions")
                .map(\.text)
                .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        }
        if c.has("fork") { fork = c.value(ChatForkSupport.self, "fork") ?? ChatForkSupport() }
    }
}

struct ChatState: Equatable, Sendable {
    var status = "starting"
    var error: String?
    var agent: ChatAgentInfo?
    var title: String?
    var cwd: String?
    var acpSessionId: String?
    var config: [ChatConfigOption] = []
    var commands: [ChatSlashCommand] = []
    var usage: ChatUsage?
    var plan: [ChatPlanEntry] = []
    var requests: [ChatPendingRequest] = []
    var queue: [ChatQueuedMessage] = []
    var promptImages = false
    var turnStartedAt: String?
    var cancelling = false
    /// The agent takes a message during a running turn ("Send now").
    var steering = false
    /// Set while the turn has ended but its work still runs.
    var background: ChatBackgroundWork?
    /// The queued automatic continue that waits for a usage limit to reset.
    var limitWait: ChatLimitWait?
    /// The agent takes `new_conversation`; an older one leaves the key out.
    var newConversation = false
    /// The prompts the agent predicts the user sends next, most likely first.
    var promptSuggestions: [String] = []
    var fork = ChatForkSupport()

    mutating func apply(_ patch: ChatStatePatch) {
        if let value = patch.status { status = value }
        if let value = patch.error { error = value }
        if let value = patch.agent { agent = value }
        if let value = patch.title { title = value }
        if let value = patch.cwd { cwd = value }
        if let value = patch.acpSessionId { acpSessionId = value }
        if let value = patch.config { config = value }
        if let value = patch.commands { commands = value }
        if let value = patch.usage { usage = value }
        if let value = patch.plan { plan = value }
        if let value = patch.requests { requests = value }
        if let value = patch.queue { queue = value }
        if let value = patch.promptImages { promptImages = value }
        if let value = patch.turnStartedAt { turnStartedAt = value }
        if let value = patch.cancelling { cancelling = value }
        if let value = patch.steering { steering = value }
        if let value = patch.background { background = value }
        if let value = patch.limitWait { limitWait = value }
        if let value = patch.newConversation { newConversation = value }
        if let value = patch.promptSuggestions { promptSuggestions = value }
        if let value = patch.fork { fork = value }
    }

    /// Unknown statuses read as `ready`.
    var phase: ChatPhase { ChatPhase(rawValue: status) ?? .ready }
}

enum ChatPhase: String, Sendable {
    case starting, loading, ready, running, error, stopped
}

/// Work a finished turn left running. An unknown value reads as none.
enum ChatBackgroundWork: String, Sendable {
    /// A workflow or a sub-agent.
    case working
    /// Only a background shell, a monitor or a scheduled wakeup.
    case waiting
}
