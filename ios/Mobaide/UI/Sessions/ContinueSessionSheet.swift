import Observation
import SwiftUI

/// Pages through the agent's stored conversations for "Continue a session".
@MainActor
@Observable
final class ContinueSessionModel {
    enum Scope: Hashable {
        case folder
        case all
    }

    private(set) var rows: [ChatStoredSession] = []
    private(set) var nextCursor: String?
    private(set) var loading = false
    private(set) var loadingMore = false
    private(set) var error: String?
    private(set) var loaded = false
    /// The agent can delete its stored sessions.
    private(set) var canDelete = false
    /// The row waiting for the user to confirm Delete.
    var pendingDelete: ChatStoredSession?
    private(set) var deleting = false
    private(set) var deleteError: String?
    var scope: Scope

    let folder: String?
    let kind: String
    private let agentAPI: AgentAPI
    @ObservationIgnored private var generation = 0

    init(agentAPI: AgentAPI, kind: String, folder: String?) {
        self.agentAPI = agentAPI
        self.kind = kind
        self.folder = folder
        scope = folder == nil ? .all : .folder
    }

    private var cwd: String? { scope == .folder ? folder : nil }

    func reload() async {
        generation += 1
        let mine = generation
        loading = true
        loadingMore = false
        error = nil
        deleteError = nil
        rows = []
        nextCursor = nil
        do {
            let page = try await agentAPI.listChatSessions(kind: kind, cwd: cwd, cursor: nil)
            guard mine == generation else { return }
            rows = page.sessions
            nextCursor = page.next_cursor
            canDelete = page.can_delete == true
        } catch {
            guard mine == generation else { return }
            self.error = Self.message(for: error)
        }
        loading = false
        loaded = true
    }

    func loadMore() async {
        guard let cursor = nextCursor, !loadingMore, !loading else { return }
        let mine = generation
        loadingMore = true
        do {
            let page = try await agentAPI.listChatSessions(kind: kind, cwd: cwd, cursor: cursor)
            guard mine == generation else { return }
            let known = Set(rows.map(\.session_id))
            rows += page.sessions.filter { !known.contains($0.session_id) }
            nextCursor = page.next_cursor
        } catch {
            guard mine == generation else { return }
            self.error = Self.message(for: error)
        }
        loadingMore = false
    }

    func askDelete(_ row: ChatStoredSession) {
        guard row.canDelete(agentCanDelete: canDelete), !deleting else { return }
        deleteError = nil
        pendingDelete = row
    }

    /// On success the row leaves the loaded list in place: a reload would
    /// restart paging from the first page.
    func confirmDelete(_ row: ChatStoredSession) async {
        guard !deleting else { return }
        pendingDelete = nil
        deleting = true
        deleteError = nil
        defer { deleting = false }
        do {
            try await agentAPI.deleteChatSession(kind: kind, sessionId: row.session_id)
            rows.removeAll { $0.session_id == row.session_id }
        } catch {
            deleteError = Self.message(for: error, action: "delete the session")
        }
    }

    private static func message(for error: Error, action: String = "list sessions") -> String {
        if let apiError = error as? APIError {
            return ConflictDetail.stringOrNil(from: apiError.errorBody) ?? "Could not \(action) (HTTP \(apiError.code))."
        }
        return "Could not reach the agent."
    }

    /// What deleting does on the PC; the agent decides between a soft and a hard delete.
    static func deleteNote(kind: String) -> String {
        kind == "codex"
            ? "Codex moves this session to its archive on your PC. It no longer shows here or in the Codex terminal app."
            : "This session is removed from your PC, also from the history of the agent's terminal app. You cannot undo this."
    }
}

/// "Continue a session": conversations stored on the PC (including ones
/// started outside Mobaide), in this folder or in all folders.
struct ContinueSessionSheet: View {
    let onPick: (ChatStoredSession) -> Void
    /// The sessions stay in sync with the agent's own apps on the PC.
    let live: Bool

    @State private var model: ContinueSessionModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors

    init(
        agentAPI: AgentAPI,
        kind: String,
        live: Bool,
        folder: String?,
        onPick: @escaping (ChatStoredSession) -> Void
    ) {
        self.onPick = onPick
        self.live = live
        _model = State(initialValue: ContinueSessionModel(agentAPI: agentAPI, kind: kind, folder: folder))
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(live
                            ? "A session that is open on your PC stays in sync: both sides see every message."
                            : "If the session is still open in a terminal on your PC, close it there first.")
                            .font(.footnote)
                            .foregroundStyle(colors.textMuted)
                        if model.folder != nil {
                            PixelSegmented(
                                [(ContinueSessionModel.Scope.folder, "This folder"), (.all, "All folders")],
                                selection: Bindable(model).scope,
                                accessibilityLabel: "Which sessions"
                            )
                        }
                    }
                    .pixelListRow()
                }
                content
            }
            .pixelList()
            .refreshable { await model.reload() }
            .navigationTitle("Continue a session")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .pixelSheet()
        .task(id: model.scope) { await model.reload() }
        .alert(
            "Delete this session?",
            isPresented: Binding(
                get: { model.pendingDelete != nil },
                set: { if !$0 { model.pendingDelete = nil } }
            ),
            presenting: model.pendingDelete
        ) { row in
            Button("Delete", role: .destructive) {
                Task { await model.confirmDelete(row) }
            }
            Button("Keep", role: .cancel) {}
        } message: { row in
            Text("\(ContinueSessionRow.title(of: row))\n\n\(ContinueSessionModel.deleteNote(kind: model.kind)) If the session is still open in a terminal on your PC, close it there first.")
        }
    }

    @ViewBuilder
    private var content: some View {
        if model.loading {
            VStack(spacing: 12) {
                PixelLoadingBlock(width: 10, height: 18)
                Text("Looking for sessions…")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 40)
            .pixelListRow()
        } else if let error = model.error, model.rows.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                PixelErrorBanner(error)
                Button("Retry") { Task { await model.reload() } }
                    .buttonStyle(.pixelSmall)
            }
            .pixelListRow()
        } else if model.loaded, model.rows.isEmpty, model.nextCursor == nil {
            PixelEmptyState(
                model.scope == .folder ? "No earlier sessions in this folder." : "No earlier sessions.",
                systemImage: "clock.arrow.circlepath"
            )
            .pixelListRow()
        } else {
            if model.deleting {
                HStack(spacing: 8) {
                    PixelLoadingBlock(width: 7, height: 13)
                    Text("Deleting…")
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
                .pixelListRow()
            }
            if let deleteError = model.deleteError {
                PixelErrorBanner(deleteError)
                    .pixelListRow()
            }
            ForEach(model.rows) { row in
                let deletable = row.canDelete(agentCanDelete: model.canDelete) && !model.deleting
                Button {
                    onPick(row)
                    dismiss()
                } label: {
                    ContinueSessionRow(row: row, showsFolder: model.scope == .all)
                }
                .buttonStyle(.plain)
                .disabled(model.deleting)
                .pixelCard()
                .pixelListRow()
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    if deletable {
                        Button(role: .destructive) {
                            model.askDelete(row)
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
                .contextMenu {
                    if deletable {
                        Button(role: .destructive) {
                            model.askDelete(row)
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
            }
            if let error = model.error {
                PixelErrorBanner(error)
                    .pixelListRow()
            }
            if model.nextCursor != nil {
                Button {
                    Task { await model.loadMore() }
                } label: {
                    HStack(spacing: 8) {
                        if model.loadingMore {
                            PixelLoadingBlock(width: 7, height: 13)
                        }
                        Text("Load more")
                    }
                }
                .buttonStyle(.pixelBlock)
                .disabled(model.loadingMore)
                .pixelListRow()
            }
        }
    }
}

private struct ContinueSessionRow: View {
    let row: ChatStoredSession
    let showsFolder: Bool

    @Environment(\.mobaideColors) private var colors

    static func title(of row: ChatStoredSession) -> String {
        guard let title = row.title, !title.isBlank else { return "Untitled session" }
        return title
    }

    private var title: String { Self.title(of: row) }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(colors.text)
                    .lineLimit(2)
                if let updated = row.updated_at.flatMap(ChatDates.parse) {
                    Text(ChatDates.relative(updated))
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                }
                if showsFolder, let cwd = row.cwd {
                    Text(cwd)
                        .font(.caption.monospaced())
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                        .truncationMode(.head)
                }
                if row.open_elsewhere == true, row.open_session_id == nil {
                    Text("Open in another app on your PC. Close it there first.")
                        .font(.caption)
                        .foregroundStyle(colors.warning)
                }
            }
            Spacer(minLength: 8)
            if row.open_session_id != nil {
                PixelChip("Open", .status(.ok))
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityHint(row.open_session_id != nil ? "Opens the session" : "Continues the session")
    }
}
