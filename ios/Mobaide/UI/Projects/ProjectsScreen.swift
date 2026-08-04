import SwiftUI

/// Projects list (Android `ProjectsScreen`), the real body for the `projects`
/// route. Cards render chips + path state; the list auto-refreshes from
/// `projects.changed`, supports swipe-delete with a 5s Undo, and drag-reorder
/// in edit mode (a single `PATCH /api/projects/order`, reconciled from the event).
struct ProjectsScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            ProjectsContent(container: container)
        }
    }
}

private struct ProjectsContent: View {
    @State private var model: ProjectsListModel
    @State private var toast: ToastMessage?
    @State private var sheet: ProjectFormMode?

    @Environment(Router.self) private var router

    let container: AppContainer

    init(container: AppContainer) {
        self.container = container
        _model = State(initialValue: ProjectsListModel(
            repository: container.projectsRepository,
            agentEventBus: container.agentEventBus
        ))
    }

    var body: some View {
        content
            .navigationTitle("Projects")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if !model.projects.isEmpty { EditButton() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { sheet = .create } label: { Image(systemName: "plus") }
                }
            }
            .refreshable { await model.refresh() }
            .sheet(item: $sheet) { mode in
                ProjectFormSheet(mode: mode, container: container, onOpenGit: { projectName in
                    sheet = nil
                    router.navigate(to: .gitManagement(projectName: projectName))
                })
            }
            .undoToast($model.undoToast) { model.undo() }
            .toast($toast)
            .task { await model.seed() }
            .task { await model.observeProjects() }
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
        if model.projects.isEmpty {
            if model.isReconnecting {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("No projects", systemImage: "folder")
            }
        } else {
            List {
                ForEach(model.projects, id: \.name) { project in
                    projectRow(project)
                }
                .onMove { source, destination in
                    model.move(from: source, to: destination)
                }
            }
            .listStyle(.plain)
        }
    }

    /// Tap edits; swipe deletes. Only a Linear-keyed project also carries the
    /// Auto Scan shortcut — the modifier is attached conditionally rather than
    /// left to render an empty menu, which would still swallow the long press
    /// on every other row.
    @ViewBuilder
    private func projectRow(_ project: ProjectInfo) -> some View {
        let row = Button {
            sheet = .edit(project)
        } label: {
            ProjectCard(project: project)
        }
        .buttonStyle(.plain)
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                model.delete(project)
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }

        if project.has_linear_key {
            row.contextMenu {
                Button {
                    router.navigate(to: .createAutoScan(projectName: project.name))
                } label: {
                    Label("Start Auto Scan…", systemImage: "bolt")
                }
            }
        } else {
            row
        }
    }
}
