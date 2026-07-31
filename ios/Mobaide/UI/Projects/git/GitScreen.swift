import SwiftUI

/// Git branches/worktrees management (Android `GitScreen`), the real body for
/// the `gitManagement` route. A row is deleted by swipe or from its context
/// menu; "Select" enters edit mode for a batch delete. Every delete runs through
/// a 5s Undo window, then branches on `force_required` (re-prompt + re-send with
/// force) and 409-busy (holder list).
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
    // Owned rather than driven by `EditButton`: this view reads edit mode for
    // its title and toolbar, which the ambient `\.editMode` binding does not
    // reliably propagate to the view that installs the toolbar.
    @State private var editMode: EditMode = .inactive

    init(container: AppContainer, projectName: String) {
        _model = State(initialValue: GitScreenModel(
            projectName: projectName,
            agentAPI: container.agentAPI,
            agentEventBus: container.agentEventBus
        ))
    }

    var body: some View {
        content
            .environment(\.editMode, $editMode)
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
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
            .onChange(of: editMode) { _, newValue in
                if !newValue.isEditing { model.clearSelection() }
            }
            .onChange(of: model.hasDeletableItems) { _, newValue in
                if !newValue { editMode = .inactive }
            }
    }

    private var title: String {
        guard editMode.isEditing else { return "Git" }
        return model.selected.isEmpty ? "Select items" : "\(model.selected.count) selected"
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .topBarTrailing) {
            if editMode.isEditing {
                Button("Delete", role: .destructive) {
                    withAnimation {
                        model.requestDelete()
                        editMode = .inactive
                    }
                }
                .disabled(model.selected.isEmpty)
            }
            if model.hasDeletableItems {
                Button(editMode.isEditing ? "Done" : "Select") {
                    withAnimation { editMode = editMode.isEditing ? .inactive : .active }
                }
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if model.visibleItems.isEmpty {
            if model.isLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("No branches or worktrees", systemImage: "arrow.triangle.branch")
            }
        } else {
            List(selection: $model.selected) {
                ForEach(model.visibleItems, id: \.id) { item in
                    row(item)
                }
            }
            .listStyle(.plain)
        }
    }

    @ViewBuilder
    private func row(_ item: GitItem) -> some View {
        if item.isDeletable {
            GitItemRow(item: item)
                .swipeActions(edge: .trailing) {
                    Button(role: .destructive) {
                        withAnimation { model.delete(item) }
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }
                }
                .contextMenu {
                    Button(role: .destructive) {
                        withAnimation { model.delete(item) }
                    } label: {
                        Label("Delete", systemImage: "trash")
                    }
                    Button {
                        model.select(item)
                        withAnimation { editMode = .active }
                    } label: {
                        Label("Select", systemImage: "checkmark.circle")
                    }
                }
        } else {
            GitItemRow(item: item)
                .selectionDisabled()
        }
    }

    private var forcePresented: Binding<Bool> {
        Binding(
            // Only the dialog's own buttons decide the outcome: SwiftUI writes
            // this binding on dismissal, which may land before the action runs.
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
