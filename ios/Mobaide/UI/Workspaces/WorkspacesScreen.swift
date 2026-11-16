import SwiftUI

/// Workspaces list (Android `WorkspacesScreen`), the real body for the `workspaces`
/// route. Cards render chips + path state; the list auto-refreshes from
/// `projects.changed`, supports swipe-delete with a 5s Undo, and drag-reorder
/// in edit mode (a single `PATCH /api/projects/order`, reconciled from the event).
struct WorkspacesScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            WorkspacesContent(container: container)
        }
    }
}

private struct WorkspacesContent: View {
    @State private var model: WorkspacesListModel
    @State private var toast: ToastMessage?
    @State private var sheet: WorkspaceFormMode?

    @Environment(Router.self) private var router

    let container: AppContainer

    init(container: AppContainer) {
        self.container = container
        _model = State(initialValue: WorkspacesListModel(
            repository: container.workspacesRepository,
            agentEventBus: container.agentEventBus
        ))
    }

    var body: some View {
        content
            .navigationTitle("Workspaces")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if !model.workspaces.isEmpty { EditButton() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { sheet = .create } label: { Image(systemName: "plus") }
                }
            }
            .refreshable { await model.refresh() }
            .sheet(item: $sheet) { mode in
                WorkspaceFormSheet(mode: mode, container: container, onOpenGit: { workspaceName in
                    sheet = nil
                    router.navigate(to: .gitManagement(workspaceName: workspaceName))
                })
            }
            .undoToast($model.undoToast) { model.undo() }
            .toast($toast)
            .task { await model.seed() }
            .task { await model.observeWorkspaces() }
            .task { await model.observeConnection() }
            .onChange(of: model.message) { _, newValue in
                if let newValue {
                    toast = ToastMessage(text: newValue)
                    model.clearMessage()
                }
            }
    }

    @ViewBuilder
    private var content: some View {
        if model.workspaces.isEmpty {
            if model.isReconnecting {
                LoadingIndicator()
                    .pixelScreenBackground()
            } else {
                ScrollView {
                    PixelEmptyState("No workspaces", systemImage: "folder")
                        .padding(16)
                }
                .pixelScreenBackground()
            }
        } else {
            List {
                ForEach(model.workspaces, id: \.name) { workspace in
                    workspaceRow(workspace)
                        .pixelCard()
                        .pixelListRow()
                }
                .onMove { source, destination in
                    model.move(from: source, to: destination)
                }
            }
            .pixelList()
        }
    }

    /// Tap edits; swipe deletes. Only a Linear-keyed workspace also carries the
    /// Auto Scan shortcut — the modifier is attached conditionally rather than
    /// left to render an empty menu, which would still swallow the long press
    /// on every other row.
    @ViewBuilder
    private func workspaceRow(_ workspace: WorkspaceInfo) -> some View {
        let row = Button {
            sheet = .edit(workspace)
        } label: {
            WorkspaceCard(workspace: workspace)
        }
        .buttonStyle(.plain)
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                model.delete(workspace)
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }

        if workspace.has_linear_key {
            row.contextMenu {
                Button {
                    router.navigate(to: .createAutoScan(workspaceName: workspace.name))
                } label: {
                    Label("Start Auto Scan…", systemImage: "bolt")
                }
            }
        } else {
            row
        }
    }
}
