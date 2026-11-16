import Foundation

/// A terminal grid in cells.
struct TerminalGrid: Equatable, Sendable {
    let cols: Int
    let rows: Int
}

/// The `pane` block of a Herdr pane session's state frame (PROTOCOL.md, "Herdr
/// dashboard"). The stream is drawn for `cols` x `rows`, the real size of the
/// pane. In control mode one client, named by `controller`, owns that size;
/// every other client still draws the given grid.
struct TerminalPaneState: Equatable, Sendable {
    static let modeWatch = "watch"
    static let modeControl = "control"

    let mode: String
    let cols: Int?
    let rows: Int?
    /// The client that controls the pane, in control mode.
    let controller: String?

    init(mode: String, cols: Int? = nil, rows: Int? = nil, controller: String? = nil) {
        self.mode = mode
        self.cols = cols
        self.rows = rows
        self.controller = controller
    }

    /// This client controls the pane: it owns the size.
    var isControl: Bool { mode == Self.modeControl && controller == PaneClient.id }

    /// Another client controls the pane; this one draws that client's grid.
    var isControlledElsewhere: Bool { mode == Self.modeControl && !isControl }

    /// The grid the agent dictates, or nil when this client owns the size.
    var grid: TerminalGrid? {
        guard !isControl, mode == Self.modeWatch || mode == Self.modeControl else { return nil }
        guard let cols, let rows, cols > 0, rows > 0 else { return nil }
        return TerminalGrid(cols: cols, rows: rows)
    }

    /// The block of a state frame body, or nil for a session that is not a Herdr pane.
    static func parse(_ stateJson: String) -> TerminalPaneState? {
        guard
            let data = stateJson.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let pane = object["pane"] as? [String: Any],
            let mode = pane["mode"] as? String, !mode.isEmpty
        else { return nil }
        func positive(_ key: String) -> Int? {
            guard let value = (pane[key] as? NSNumber)?.intValue, value > 0 else { return nil }
            return value
        }
        let controller = (pane["controller"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        return TerminalPaneState(
            mode: mode, cols: positive("cols"), rows: positive("rows"), controller: controller
        )
    }
}

/// What changes when the terminal shows a Herdr pane instead of a session. A
/// pane is not in the session list, so the sessions drawer and the git menu
/// are off, and the top bar gets the pane's own actions.
struct TerminalPaneChrome {
    let title: String
    /// One line above the terminal that says what typing does in this mode.
    let notice: String?
    /// The server can give control of a pane: the mode switch is shown.
    let canControl: Bool
    let controlling: Bool
    let controlBusy: Bool
    let onToggleControl: () -> Void
    let onOpenHistory: () -> Void
}
