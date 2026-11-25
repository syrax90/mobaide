import Foundation

/// One row of the chat timeline. In focus view the work between answers folds
/// into a `steps` row; its items follow it as nested items while it is open.
enum ChatTimelineRow: Identifiable, Equatable {
    case item(ChatItem, nested: Bool)
    /// `live`: the run is the newest thing in a running turn. `current`: what
    /// the newest step does ("Thinking", a tool title), nil unless live.
    case steps(id: String, count: Int, live: Bool, current: String?, open: Bool)

    var id: String {
        switch self {
        case .item(let item, _): return item.id
        case .steps(let id, _, _, _, _): return id
        }
    }
}

/// A subagent card with what it shows inside: its tool calls and its nested
/// cards, both in timeline order.
struct ChatSubagentNode: Equatable {
    let item: ChatItem
    let subagent: ChatSubagent
    let tools: [ChatItem]
    let children: [ChatSubagentNode]
}

/// Which items the subagent cards take out of the main flow. A tool goes into
/// the card its `subagent_id` names, a card into its parent's card; with no
/// such card present (or a parent loop) the item stays in the main flow.
struct ChatSubagentLayout {
    /// The items of the main flow, in order.
    let visible: [ChatItem]
    /// Every subagent card by item id.
    let nodes: [String: ChatSubagentNode]

    init(_ items: [ChatItem]) {
        var subagents: [String: ChatSubagent] = [:]
        for item in items {
            if case .subagent(let subagent) = item.body, subagents[subagent.subagentId] == nil {
                subagents[subagent.subagentId] = subagent
            }
        }
        guard !subagents.isEmpty else {
            self.visible = items
            self.nodes = [:]
            return
        }
        var parentOf: [String: String] = [:]
        for (id, subagent) in subagents {
            guard let parent = subagent.parentSubagentId, subagents[parent] != nil else { continue }
            var seen: Set<String> = [id]
            var cursor: String? = parent
            var loops = false
            while let current = cursor {
                if !seen.insert(current).inserted {
                    loops = true
                    break
                }
                cursor = subagents[current]?.parentSubagentId.flatMap { subagents[$0] != nil ? $0 : nil }
            }
            if !loops { parentOf[id] = parent }
        }
        var tools: [String: [ChatItem]] = [:]
        var children: [String: [ChatItem]] = [:]
        var visible: [ChatItem] = []
        var cards: [ChatItem] = []
        for item in items {
            switch item.body {
            case .tool(let call):
                if let owner = call.subagentId, subagents[owner] != nil {
                    tools[owner, default: []].append(item)
                } else {
                    visible.append(item)
                }
            case .subagent(let subagent):
                cards.append(item)
                if let parent = parentOf[subagent.subagentId] {
                    children[parent, default: []].append(item)
                } else {
                    visible.append(item)
                }
            default:
                visible.append(item)
            }
        }
        var nodes: [String: ChatSubagentNode] = [:]
        func node(_ item: ChatItem, _ subagent: ChatSubagent) -> ChatSubagentNode {
            if let built = nodes[item.id] { return built }
            let nested = (children[subagent.subagentId] ?? []).compactMap { child -> ChatSubagentNode? in
                guard case .subagent(let childSubagent) = child.body else { return nil }
                return node(child, childSubagent)
            }
            let built = ChatSubagentNode(
                item: item,
                subagent: subagent,
                tools: tools[subagent.subagentId] ?? [],
                children: nested
            )
            nodes[item.id] = built
            return built
        }
        for item in cards {
            if case .subagent(let subagent) = item.body {
                _ = node(item, subagent)
            }
        }
        self.visible = visible
        self.nodes = nodes
    }
}

/// Focus view: folds thinking, tool calls, and agent messages that more work
/// follows in the same turn; everything else shows in place. A turn ends at the
/// next user message or notice: a turn sent from another app on the PC starts
/// with a notice, and must not fold the answer before it.
enum ChatFocusView {
    static func stepsId(firstItemId: String) -> String { "steps:\(firstItemId)" }

    static func stepsLabel(_ count: Int) -> String { count == 1 ? "1 step" : "\(count) steps" }

    /// Items inside subagent cards are taken out first; a card itself never
    /// folds, and counts as work after an agent message before it.
    static func rows(
        _ timelineItems: [ChatItem],
        focus: Bool,
        running: Bool,
        waitingToolIds: Set<String>,
        open: Set<String>,
        layout: ChatSubagentLayout? = nil
    ) -> [ChatTimelineRow] {
        let items = (layout ?? ChatSubagentLayout(timelineItems)).visible
        guard focus else { return items.map { .item($0, nested: false) } }
        var folded = [Bool](repeating: false, count: items.count)
        var workAfter = false
        for index in items.indices.reversed() {
            let item = items[index]
            switch item.body {
            case .user, .notice:
                workAfter = false
            case .agent:
                folded[index] = workAfter
            case .thought:
                folded[index] = true
                workAfter = true
            case .tool:
                folded[index] = !waitingToolIds.contains(item.id)
                workAfter = true
            case .subagent:
                workAfter = true
            default:
                break
            }
        }
        var rows: [ChatTimelineRow] = []
        rows.reserveCapacity(items.count)
        var index = 0
        while index < items.count {
            guard folded[index] else {
                rows.append(.item(items[index], nested: false))
                index += 1
                continue
            }
            let start = index
            while index < items.count, folded[index] {
                index += 1
            }
            let group = items[start..<index]
            guard let first = group.first, let newest = group.last else { continue }
            let id = stepsId(firstItemId: first.id)
            let live = running && index == items.count
            let isOpen = open.contains(id)
            rows.append(.steps(
                id: id,
                count: group.count,
                live: live,
                current: live ? stepLabel(newest) : nil,
                open: isOpen
            ))
            if isOpen {
                rows.append(contentsOf: group.map { .item($0, nested: true) })
            }
        }
        return rows
    }

    private static func stepLabel(_ item: ChatItem) -> String {
        switch item.body {
        case .tool(let tool): return tool.title.isEmpty ? "Tool call" : tool.title
        case .thought: return "Thinking"
        case .agent: return "Writing"
        default: return "Working"
        }
    }
}
