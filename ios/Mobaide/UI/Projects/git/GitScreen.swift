import SwiftUI

/// Git branches/worktrees management (Android `GitScreen`), the real body for
/// the `gitManagement` route. A row is deleted by swipe or from its context
/// menu; "Select" enters edit mode for a batch delete. Every delete runs through
/// a 5s Undo window, then branches on `force_required` (re-prompt + re-send with
/// force) and 409-busy (holder list). Once the agent advertises
/// `capabilities.git.branches` the remote-tracking branches sit in a collapsed
/// section, "+" opens the New branch sheet, and a `branch_only` or remote row
/// offers Check out (leading swipe or context menu) behind a confirmation.
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
            .confirmationDialog(
                "Check out \(model.checkoutPrompt.map(GitItemDisplay.primaryLabel) ?? "")?",
                isPresented: checkoutPresented,
                titleVisibility: .visible,
                presenting: model.checkoutPrompt
            ) { _ in
                Button("Check out") { model.confirmCheckout() }
                Button("Cancel", role: .cancel) { model.dismissCheckoutPrompt() }
            } message: { item in
                Text(GitItemDisplay.checkoutConfirmMessage(
                    branch: GitItemDisplay.primaryLabel(item),
                    projectName: model.projectName
                ))
            }
            .sheet(isPresented: newBranchPresented) {
                GitNewBranchSheet(model: model)
            }
            .undoToast($model.undoToast) { model.undo() }
            .toast($toast)
            .task { await model.load() }
            .task { await model.observeGitChanged() }
            .task { await model.loadCapabilities() }
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
            } else if model.canManageBranches {
                Button {
                    model.openNewBranch()
                } label: {
                    Image(systemName: "plus")
                }
                .disabled(model.isCheckingOut)
                .accessibilityLabel("New branch")
                .accessibilityIdentifier("git-new-branch")
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
                LoadingIndicator()
                    .pixelScreenBackground()
            } else {
                ScrollView {
                    PixelEmptyState("No branches or worktrees", systemImage: "arrow.triangle.branch")
                        .padding(16)
                }
                .pixelScreenBackground()
            }
        } else if model.filterable {
            list
                .searchable(
                    text: $model.filter,
                    placement: .navigationBarDrawer(displayMode: .always),
                    prompt: "Filter branches"
                )
                // Distinct from the empty-repository state above: a filter
                // that matched nothing is fixed by editing the filter.
                .overlay {
                    if model.localItems.isEmpty && model.remoteItems.isEmpty {
                        ContentUnavailableView.search(text: model.filter)
                    }
                }
        } else {
            list
        }
    }

    private var list: some View {
        List(selection: $model.selected) {
            Section {
                ForEach(model.localItems, id: \.id) { item in
                    row(item)
                        .pixelCard(selected: editMode.isEditing && model.selected.contains(item.id))
                        .pixelListRow()
                }
            }
            if !model.remoteItems.isEmpty {
                Section(header: remoteHeader) {
                    if model.remoteOpen {
                        ForEach(model.remoteItems, id: \.id) { item in
                            row(item)
                                .pixelListRow()
                        }
                    }
                }
            }
        }
        .pixelList()
    }

    private var remoteHeader: some View {
        Button {
            withAnimation { model.toggleRemote() }
        } label: {
            HStack {
                PixelSectionHeader("Remote branches")
                Text("\(model.remoteItems.count)")
                    .chromeText()
                    .foregroundStyle(colors.textMuted)
                Spacer()
                Image(systemName: model.remoteOpen ? "chevron.up" : "chevron.down")
                    .foregroundStyle(colors.textMuted)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(model.remoteOpen ? "Collapse remote branches" : "Expand remote branches")
        .accessibilityIdentifier("git-remote-header")
    }

    /// In edit mode the row is plain content so the `List` owns the tap; a
    /// remote-tracking row is never selectable. Outside it, a deletable row
    /// keeps its trailing Delete swipe, and every row that can be checked out
    /// gets a leading Check out swipe plus the same entry in its context menu.
    @ViewBuilder
    private func row(_ item: GitItem) -> some View {
        let checkoutable = model.canManageBranches && item.canCheckOut
        if editMode.isEditing {
            if item.isDeletable {
                GitItemRow(item: item)
            } else {
                GitItemRow(item: item)
                    .selectionDisabled()
            }
        } else if item.isDeletable || checkoutable {
            GitItemRow(item: item)
                .selectionDisabled(!item.isDeletable)
                .swipeActions(edge: .leading, allowsFullSwipe: false) {
                    if checkoutable {
                        checkoutButton(item)
                            .tint(colors.accent)
                    }
                }
                .swipeActions(edge: .trailing) {
                    if item.isDeletable {
                        Button(role: .destructive) {
                            withAnimation { model.delete(item) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
                .contextMenu {
                    if checkoutable {
                        checkoutButton(item)
                    }
                    if item.isDeletable {
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
                }
                .disabled(model.isCheckingOut && checkoutable)
        } else {
            GitItemRow(item: item)
                .selectionDisabled()
        }
    }

    private func checkoutButton(_ item: GitItem) -> some View {
        Button {
            model.requestCheckout(item)
        } label: {
            Label("Check out", systemImage: "arrow.right.circle")
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

    private var checkoutPresented: Binding<Bool> {
        Binding(
            // Clears only the prompt: the row the action needs stays in the
            // model's `pendingCheckout` until Check out or Cancel runs.
            get: { model.checkoutPrompt != nil },
            set: { if !$0 { model.checkoutPrompt = nil } }
        )
    }

    private var newBranchPresented: Binding<Bool> {
        Binding(
            get: { model.newBranch != nil },
            set: { if !$0 { model.closeNewBranch() } }
        )
    }

    static func busyLine(_ holder: BusyHolder) -> String {
        switch holder.kind {
        case "session":
            return "Session: \(holder.cwd ?? "?")"
        case "auto_scan":
            return "Auto-scan \(holder.project_name ?? "?"): \(holder.worktree_path ?? "?")"
        default:
            return "\(holder.kind): \(holder.cwd ?? holder.worktree_path ?? "?")"
        }
    }
}
