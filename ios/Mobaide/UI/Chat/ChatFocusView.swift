import Foundation

/// One row of the chat timeline. In focus view the work between answers folds
/// into a `steps` row; its items follow it as nested items while it is open.
enum ChatTimelineRow: Identifiable, Equatable {
    case item(ChatItem, nested: Bool)
    /// `live`: the run is the newest thing in a running turn. `current`: what
    /// the newest step does ("Thinking", a tool title), nil unless live.
    case steps(id: String, count: Int, live: Bool, current: String?, open: Bool)
    /// Two or more subagent cards behind one collapsible head.
    case subagents(id: String, cards: [ChatSubagentNode], open: Bool)

    var id: String {
        switch self {
        case .item(let item, _): return item.id
        case .steps(let id, _, _, _, _): return id
        case .subagents(let id, _, _): return id
        }
    }
}

/// Sibling subagent cards as shown: a group of the calm ones, or one card.
enum ChatSubagentEntry: Equatable, Identifiable {
    case card(ChatSubagentNode)
    case group(id: String, cards: [ChatSubagentNode])

    var id: String {
        switch self {
        case .card(let node): return node.item.id
        case .group(let id, _): return id
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

    static func subagentGroupId(firstCardId: String) -> String { "subagents:\(firstCardId)" }

    static func subagentsLabel(_ count: Int) -> String { count == 1 ? "1 subagent" : "\(count) subagents" }

    /// The card, a tool call it waits on, or a card nested in it waits for the user.
    static func needsYou(_ node: ChatSubagentNode, waitingToolIds: Set<String>) -> Bool {
        node.subagent.state == "requires_action"
            || node.tools.contains { waitingToolIds.contains($0.id) }
            || node.children.contains { needsYou($0, waitingToolIds: waitingToolIds) }
    }

    /// Two or more calm cards form one group, keyed by the first card of all;
    /// the cards that need the user follow it on their own, so a request is
    /// never hidden. With fewer than two calm cards every card shows on its
    /// own, in order.
    static func subagentEntries(_ cards: [ChatSubagentNode], waitingToolIds: Set<String>) -> [ChatSubagentEntry] {
        let urgent = cards.filter { needsYou($0, waitingToolIds: waitingToolIds) }
        let calm = cards.filter { !needsYou($0, waitingToolIds: waitingToolIds) }
        guard let first = cards.first, calm.count >= 2 else { return cards.map { .card($0) } }
        return [.group(id: subagentGroupId(firstCardId: first.item.id), cards: calm)] + urgent.map { .card($0) }
    }

    /// "2 running · 5 done · 1 failed": the states of a group's cards, busiest first.
    static func subagentGroupSummary(_ cards: [ChatSubagentNode]) -> String {
        var needs = 0, running = 0, done = 0, failed = 0, stopped = 0
        for card in cards {
            switch card.subagent.state {
            case "requires_action": needs += 1
            case "running": running += 1
            case "idle":
                switch card.subagent.stopReason {
                case "cancelled": stopped += 1
                case "error": failed += 1
                default: done += 1
                }
            default: break
            }
        }
        return [
            needs > 0 ? "\(needs) need you" : nil,
            running > 0 ? "\(running) running" : nil,
            done > 0 ? "\(done) done" : nil,
            failed > 0 ? "\(failed) failed" : nil,
            stopped > 0 ? "\(stopped) stopped" : nil,
        ].compactMap { $0 }.joined(separator: " · ")
    }

    /// The first line of a result as plain text, for a collapsed card; nil when it is blank.
    static func resultPreview(_ result: String?) -> String? {
        guard let result else { return nil }
        for raw in result.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
                .replacingOccurrences(of: #"^\s*(#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)"#, with: "", options: .regularExpression)
                .replacingOccurrences(of: #"\*\*|__|`"#, with: "", options: .regularExpression)
                .trimmingCharacters(in: .whitespaces)
            if !line.isEmpty, line.range(of: #"^(-{3,}|\*{3,}|_{3,})$"#, options: .regularExpression) == nil {
                return line
            }
        }
        return nil
    }

    /// Runs of two or more cards with only thinking and tool calls between them, by the first card's id.
    private static func subagentRuns(_ items: [ChatItem], layout: ChatSubagentLayout) -> [String: [ChatSubagentNode]] {
        var runs: [String: [ChatSubagentNode]] = [:]
        var run: [ChatSubagentNode] = []
        func close() {
            if run.count >= 2, let first = run.first { runs[first.item.id] = run }
            run = []
        }
        for item in items {
            switch item.body {
            case .subagent:
                if let node = layout.nodes[item.id] { run.append(node) }
            case .thought, .tool:
                break
            default:
                close()
            }
        }
        close()
        return runs
    }

    /// Items inside subagent cards are taken out first; a card itself never
    /// folds, and counts as work after an agent message before it. Cards with
    /// only thinking and tool calls between them form a run; a run of two or
    /// more shows at its first card's place through `subagentEntries`.
    static func rows(
        _ timelineItems: [ChatItem],
        focus: Bool,
        running: Bool,
        waitingToolIds: Set<String>,
        open: Set<String>,
        layout: ChatSubagentLayout? = nil
    ) -> [ChatTimelineRow] {
        let layout = layout ?? ChatSubagentLayout(timelineItems)
        let runs = subagentRuns(layout.visible, layout: layout)
        let merged = Set(runs.values.flatMap { $0.dropFirst().map(\.item.id) })
        let items = merged.isEmpty ? layout.visible : layout.visible.filter { !merged.contains($0.id) }
        func row(_ item: ChatItem) -> [ChatTimelineRow] {
            guard let run = runs[item.id] else { return [.item(item, nested: false)] }
            return subagentEntries(run, waitingToolIds: waitingToolIds).map { entry in
                switch entry {
                case .card(let node): return .item(node.item, nested: false)
                case .group(let id, let cards): return .subagents(id: id, cards: cards, open: open.contains(id))
                }
            }
        }
        guard focus else { return items.flatMap(row) }
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
                rows.append(contentsOf: row(items[index]))
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
