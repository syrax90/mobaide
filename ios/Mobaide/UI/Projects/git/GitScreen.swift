import SwiftUI

/// Git branches/worktrees management (Android `GitScreen`), the real body for
/// the `gitManagement` route. Long-press enters multi-select; the batch delete
/// runs through a 5s Undo window, then branches on `force_required` (re-prompt +
/// re-send with force) and 409-busy (holder list).
struct GitScreen: View {
    let projectName: String

    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            GitContent(container: container, projectName: projectName)
        }
    }
}

private struct GitContent: View {
    @State private var model: GitScreenModel
    @State private var toast: ToastMessage?

    @Environment(\.mobaideColors) private var colors

    init(container: AppContainer, projectName: String) {
        _model = State(initialValue: GitScreenModel(
            projectName: projectName,
            agentAPI: container.agentAPI,
            agentEventBus: container.agentEventBus
        ))
    }

    var body: some View {
        content
            .navigationTitle(model.inSelectionMode ? "\(model.selected.count) selected" : "Git")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if model.inSelectionMode {
                    ToolbarItem(placement: .topBarLeading) {
                        Button("Close") { model.exitSelection() }
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Delete", role: .destructive) { model.requestDelete() }
                            .disabled(model.selected.isEmpty)
                    }
                }
            }
            .refreshable { await model.load() }
            .confirmationDialog(
                "Force delete?",
                isPresented: forcePresented,
                presenting: model.forcePrompt
            ) { _ in
                Button("Force delete", role: .destructive) { model.confirmForceDelete() }
                Button("Cancel", role: .cancel) { model.dismissForcePrompt() }
            } message: { prompt in
                Text("These items have unmerged commits or uncommitted changes and can't be deleted normally: \(prompt.refs.joined(separator: ", ")). Force-delete permanently discards that work.")
            }
            .alert(
                "Item is in use",
                isPresented: busyPresented,
                presenting: model.busyDialog
            ) { _ in
                Button("OK") { model.dismissBusy() }
            } message: { holders in
                Text("Stop the holder, then try again.\n\n" + holders.map(Self.busyLine).joined(separator: "\n"))
            }
            .undoToast($model.undoToast) { model.undo() }
            .toast($toast)
            .task { await model.load() }
            .task { await model.observeGitChanged() }
            .onChange(of: model.message) { _, newValue in
                if let newValue {
                    toast = ToastMessage(text: newValue)
                    model.message = nil
                }
            }
    }

    @ViewBuilder
    private var content: some View {
        if model.items.isEmpty {
            if model.isLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("No branches or worktrees", systemImage: "arrow.triangle.branch")
            }
        } else {
            List {
                ForEach(model.items, id: \.id) { item in
                    GitItemRow(
                        item: item,
                        inSelectionMode: model.inSelectionMode,
                        isSelected: model.selected.contains(item.id)
                    )
                    .listRowBackground(model.selected.contains(item.id) ? colors.primary.opacity(0.12) : nil)
                    .contentShape(Rectangle())
                    .onTapGesture { model.rowTapped(item) }
                    .onLongPressGesture { model.rowLongPressed(item) }
                }
            }
            .listStyle(.plain)
        }
    }

    private var forcePresented: Binding<Bool> {
        Binding(
            get: { model.forcePrompt != nil },
            set: { if !$0 { model.forcePrompt = nil } }
        )
    }

    private var busyPresented: Binding<Bool> {
        Binding(
            get: { model.busyDialog != nil },
            set: { if !$0 { model.busyDialog = nil } }
        )
    }

    static func busyLine(_ holder: BusyHolder) -> String {
        switch holder.kind {
        case "session":
            return "Session \(holder.session_id ?? "?"): \(holder.cwd ?? "?")"
        case "auto_scan":
            return "Auto-scan \(holder.project_name ?? "?"): \(holder.worktree_path ?? "?")"
        default:
            return "\(holder.kind): \(holder.cwd ?? holder.worktree_path ?? "?")"
        }
    }
}
