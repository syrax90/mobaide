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

/// Focus view: folds thinking, tool calls, and agent messages that more work
/// follows in the same turn; everything else shows in place. A turn ends at the
/// next user message or notice: a turn sent from another app on the PC starts
/// with a notice, and must not fold the answer before it.
enum ChatFocusView {
    static func stepsId(firstItemId: String) -> String { "steps:\(firstItemId)" }

    static func stepsLabel(_ count: Int) -> String { count == 1 ? "1 step" : "\(count) steps" }

    static func rows(
        _ items: [ChatItem],
        focus: Bool,
        running: Bool,
        waitingToolIds: Set<String>,
        open: Set<String>
    ) -> [ChatTimelineRow] {
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
