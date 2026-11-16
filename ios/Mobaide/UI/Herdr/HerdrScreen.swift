import SwiftUI

/// The `herdr` route: the agents that need the user, then every server's tree
/// (Android `HerdrScreen`). With both ids it opens that pane at once: the
/// route of a tapped alert.
struct HerdrScreen: View {
    var openServer: String? = nil
    var openPane: String? = nil
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            HerdrContent(container: container, openServer: openServer, openPane: openPane)
        }
    }
}

private struct HerdrContent: View {
    @State private var model: HerdrModel
    /// The alert's pane is opened once, not on each return to this screen.
    @State private var openedDeepLink = false
    @State private var renameText = ""
    private let openServer: String?
    private let openPane: String?

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors

    init(container: AppContainer, openServer: String?, openPane: String?) {
        self.openServer = openServer
        self.openPane = openPane
        _model = State(initialValue: HerdrModel(
            repository: container.herdrRepository,
            seenStore: container.herdrSeenStore,
            projectsRepository: container.projectsRepository
        ))
    }

    var body: some View {
        content
            .navigationTitle("Herdr")
            .navigationBarTitleDisplayMode(.inline)
            .refreshable { await model.refresh() }
            .toast(Bindable(model).message)
            .task { await model.observe() }
            .task { await model.refresh() }
            .task { await model.observeProjects() }
            .task {
                guard !openedDeepLink, let openServer, let openPane else { return }
                openedDeepLink = true
                await model.openPane(serverId: openServer, paneId: openPane)
            }
            .onChange(of: model.openRequest) { _, request in
                guard let request else { return }
                model.openRequest = nil
                model.dialog = nil
                router.navigate(to: .herdrPane(
                    sessionId: request.sessionId, serverId: request.serverId, paneId: request.paneId
                ))
            }
            .onChange(of: model.dialog) { _, dialog in
                if case .rename(_, _, _, let current) = dialog { renameText = current }
            }
            .sheet(item: createBinding) { request in
                HerdrCreateSheet(
                    forWorkspace: request.workspaceId == nil,
                    noun: request.server.product == HerdrServer.productCore ? "terminal" : "workspace",
                    projects: model.projects,
                    agents: request.server.features.agents,
                    busy: model.busy,
                    error: model.actionError
                ) { values in
                    Task {
                        await model.create(HerdrCreateRequest(
                            server_id: request.server.server_id,
                            placement: request.workspaceId == nil
                                ? HerdrCreateRequest.placementWorkspace
                                : HerdrCreateRequest.placementTab,
                            workspace_id: request.workspaceId,
                            cwd: values.cwd,
                            label: values.label,
                            agent: values.agent
                        ))
                    }
                }
            }
            .alert(
                "Rename \(renameRequest?.target.rawValue ?? "")",
                isPresented: presented(renameRequest != nil),
                presenting: renameRequest
            ) { request in
                TextField("Name", text: $renameText)
                Button("Rename") {
                    let label = String(renameText.trimmingCharacters(in: .whitespacesAndNewlines).prefix(HerdrLimits.labelMaxChars))
                    guard !label.isEmpty, label != request.current else { return }
                    Task {
                        await model.rename(
                            serverId: request.server.server_id, target: request.target, id: request.id, label: label
                        )
                    }
                }
                Button("Cancel", role: .cancel) {}
            }
            // Herdr closes at once and cannot undo it, so the question is asked
            // here. It says what is lost: the programs in the panes stop.
            .confirmationDialog(
                "Close \(closeRequest?.target.rawValue ?? "")?",
                isPresented: presented(closeRequest != nil),
                titleVisibility: .visible,
                presenting: closeRequest
            ) { request in
                Button("Close", role: .destructive) {
                    Task {
                        await model.close(serverId: request.server.server_id, target: request.target, id: request.id)
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: { request in
                Text(
                    "Close “\(request.label)”? "
                        + (request.target == .pane
                            ? "The program in this pane stops."
                            : "Every pane in this \(request.target.rawValue) closes and its programs stop.")
                )
            }
            .confirmationDialog(
                "Start an agent",
                isPresented: presented(startAgentRequest != nil),
                titleVisibility: .visible,
                presenting: startAgentRequest
            ) { request in
                ForEach(request.server.features.agents, id: \.id) { kind in
                    Button(kind.label) {
                        Task {
                            await model.startAgent(
                                serverId: request.server.server_id, paneId: request.paneId, agent: kind.id
                            )
                        }
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: { _ in
                Text("The agent starts in this pane's terminal.")
            }
    }

    // MARK: - Dialog plumbing

    private struct CreateRequest: Identifiable {
        let server: HerdrServer
        let workspaceId: String?
        var id: String { "\(server.server_id)\u{0}\(workspaceId ?? "")" }
    }

    private struct RenameRequest {
        let server: HerdrServer
        let target: HerdrTarget
        let id: String
        let current: String
    }

    private struct CloseRequest {
        let server: HerdrServer
        let target: HerdrTarget
        let id: String
        let label: String
    }

    private struct StartAgentRequest {
        let server: HerdrServer
        let paneId: String
    }

    private var createBinding: Binding<CreateRequest?> {
        Binding(
            get: {
                if case .create(let server, let workspaceId) = model.dialog {
                    return CreateRequest(server: server, workspaceId: workspaceId)
                }
                return nil
            },
            set: { if $0 == nil, case .create = model.dialog { model.dialog = nil } }
        )
    }

    private var renameRequest: RenameRequest? {
        if case .rename(let server, let target, let id, let current) = model.dialog {
            return RenameRequest(server: server, target: target, id: id, current: current)
        }
        return nil
    }

    private var closeRequest: CloseRequest? {
        if case .close(let server, let target, let id, let label) = model.dialog {
            return CloseRequest(server: server, target: target, id: id, label: label)
        }
        return nil
    }

    private var startAgentRequest: StartAgentRequest? {
        if case .startAgent(let server, let paneId) = model.dialog {
            return StartAgentRequest(server: server, paneId: paneId)
        }
        return nil
    }

    /// The binding of an alert or a question: closing it clears the dialog.
    private func presented(_ shown: Bool) -> Binding<Bool> {
        Binding(
            get: { shown },
            set: { if !$0 { model.dialog = nil } }
        )
    }

    // MARK: - Content

    @ViewBuilder
    private var content: some View {
        let rows = model.rows
        if model.servers.isEmpty {
            if model.loaded {
                ScrollView {
                    PixelEmptyState(
                        "No Herdr server runs on this computer",
                        systemImage: "rectangle.split.2x2",
                        description: "Start Herdr there, and it shows up here."
                    )
                    .padding(16)
                    .accessibilityIdentifier("herdr-empty")
                }
                .pixelScreenBackground()
            } else {
                LoadingIndicator()
                    .pixelScreenBackground()
            }
        } else {
            tree(rows: rows, agents: HerdrDisplay.agentRows(rows))
        }
    }

    private func tree(rows: [HerdrPaneRow], agents: [HerdrPaneRow]) -> some View {
        List {
            Section {
                Text(HerdrDisplay.summary(rows))
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
                    .pixelListRow()
            }
            if !agents.isEmpty {
                Section(header: PixelSectionHeader("Agents")) {
                    ForEach(agents) { row in
                        paneCard(row, showWorkspace: true)
                    }
                }
            }
            ForEach(model.servers, id: \.server_id) { server in
                Section(header: serverHeader(server)) {
                    if !server.isReady {
                        note(server.message ?? "This Herdr server is not ready.")
                    } else if server.workspaces.isEmpty {
                        note("Nothing is open in this Herdr server.")
                    }
                    ForEach(server.workspaces, id: \.workspace_id) { workspace in
                        groupHeading(
                            workspace.label,
                            secondary: false,
                            hasActions: server.features.new_tab || server.features.rename
                                || server.features.close_group
                        ) {
                            workspaceMenu(server, workspace)
                        }
                        ForEach(workspace.tabs, id: \.tab_id) { tab in
                            // A workspace with one tab reads as a flat list:
                            // the tab level adds nothing.
                            if workspace.tabs.count > 1 {
                                groupHeading(
                                    tab.label,
                                    secondary: true,
                                    hasActions: server.features.rename || server.features.close_group
                                ) {
                                    tabMenu(server, tab)
                                }
                            }
                            ForEach(rows.filter {
                                $0.server.server_id == server.server_id
                                    && $0.workspace.workspace_id == workspace.workspace_id
                                    && $0.tab.tab_id == tab.tab_id
                            }) { row in
                                paneCard(row, showWorkspace: false)
                            }
                        }
                    }
                }
            }
        }
        .pixelList()
        .accessibilityIdentifier("herdr-screen")
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
            .frame(maxWidth: .infinity, alignment: .leading)
            .pixelCard()
            .pixelListRow()
    }

    private func serverHeader(_ server: HerdrServer) -> some View {
        HStack(spacing: 8) {
            PixelSectionHeader(
                [HerdrDisplay.serverLabel(server, among: model.servers), server.version]
                    .compactMap { $0 }
                    .joined(separator: " ")
            )
            Spacer()
            if server.isReady, server.features.new_workspace {
                Button(server.product == HerdrServer.productCore ? "New terminal" : "New workspace") {
                    model.dialog = .create(server: server, workspaceId: nil)
                }
                .buttonStyle(.pixelSmall)
                .accessibilityIdentifier("herdr-new-workspace")
            }
        }
    }

    private func groupHeading<Items: View>(
        _ label: String, secondary: Bool, hasActions: Bool, @ViewBuilder menu: () -> Items
    ) -> some View {
        HStack(spacing: 8) {
            Text(label)
                .font(secondary ? .footnote : .subheadline.weight(.semibold))
                .foregroundStyle(secondary ? colors.textMuted : colors.text)
                .lineLimit(1)
            Spacer()
            actionMenu("Actions for \(label)", visible: hasActions, items: menu)
        }
        .frame(minHeight: 44)
        .pixelListRow()
    }

    /// The `⋯` of a row. Hidden when the server offers no action for it.
    @ViewBuilder
    private func actionMenu<Items: View>(
        _ label: String, visible: Bool, @ViewBuilder items: () -> Items
    ) -> some View {
        if visible {
            Menu {
                items()
            } label: {
                Image(systemName: "ellipsis")
                    .foregroundStyle(colors.textMuted)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .menuOrder(.fixed)
            .accessibilityLabel(label)
        }
    }

    private func paneCard(_ row: HerdrPaneRow, showWorkspace: Bool) -> some View {
        let pane = row.pane
        let title = HerdrDisplay.paneTitle(pane)
        let kind = pane.agent.map(HerdrDisplay.agentLabel) ?? "Terminal"
        let subtitle = showWorkspace && !row.workspace.label.isBlank ? "\(kind) · \(row.workspace.label)" : kind
        return HStack(spacing: 4) {
            Button {
                Task { await model.openPane(serverId: row.server.server_id, paneId: pane.pane_id) }
            } label: {
                HStack(spacing: 12) {
                    HerdrStatusMark(status: row.status)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(title)
                            .font(.body.weight(.semibold))
                            .foregroundStyle(colors.text)
                            .lineLimit(1)
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(colors.textMuted)
                            .lineLimit(1)
                        if let cwd = pane.cwd {
                            Text(cwd)
                                .font(.caption2)
                                .foregroundStyle(colors.textMuted)
                                .lineLimit(1)
                                .truncationMode(.head)
                        }
                    }
                    Spacer()
                    if model.openingKey == row.id {
                        PixelLoadingBlock(width: 8, height: 14)
                            .accessibilityLabel("Opening")
                            .accessibilityIdentifier("herdr-pane-opening")
                    } else if let chip = HerdrDisplay.chip(row.status) {
                        PixelChip(chip.text, .status(chip.tone))
                            .fixedSize()
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("herdr-pane-row")
            actionMenu("Actions for \(title)", visible: paneHasActions(row)) { paneMenu(row) }
        }
        .pixelCard()
        .pixelListRow()
    }

    // MARK: - Menus

    @ViewBuilder
    private func paneMenu(_ row: HerdrPaneRow) -> some View {
        let server = row.server
        let pane = row.pane
        let features = server.features
        let title = HerdrDisplay.paneTitle(pane)
        if row.status == .done, pane.turn != nil {
            Button("Mark as seen") { model.markSeen(row) }
        }
        if features.split {
            Button("Split right") { split(row, HerdrCreateRequest.placementSplitRight) }
            Button("Split down") { split(row, HerdrCreateRequest.placementSplitDown) }
        }
        if pane.agent == nil, !features.agents.isEmpty {
            Button("Start an agent…") { model.dialog = .startAgent(server: server, paneId: pane.pane_id) }
        }
        if features.rename {
            Button("Rename…") {
                model.dialog = .rename(server: server, target: .pane, id: pane.pane_id, current: title)
            }
        }
        if features.close_pane {
            Button("Close pane…", role: .destructive) {
                model.dialog = .close(server: server, target: .pane, id: pane.pane_id, label: title)
            }
        }
    }

    /// Whether `paneMenu` has an item; the two read the same conditions.
    private func paneHasActions(_ row: HerdrPaneRow) -> Bool {
        let features = row.server.features
        return (row.status == .done && row.pane.turn != nil) || features.split
            || (row.pane.agent == nil && !features.agents.isEmpty) || features.rename || features.close_pane
    }

    private func split(_ row: HerdrPaneRow, _ placement: String) {
        Task {
            await model.create(HerdrCreateRequest(
                server_id: row.server.server_id, placement: placement, pane_id: row.pane.pane_id
            ))
        }
    }

    @ViewBuilder
    private func workspaceMenu(_ server: HerdrServer, _ workspace: HerdrWorkspace) -> some View {
        let id = workspace.workspace_id
        if server.features.new_tab {
            Button("New tab…") { model.dialog = .create(server: server, workspaceId: id) }
        }
        if server.features.rename {
            Button("Rename…") {
                model.dialog = .rename(server: server, target: .workspace, id: id, current: workspace.label)
            }
        }
        if server.features.close_group {
            Button("Close workspace…", role: .destructive) {
                model.dialog = .close(server: server, target: .workspace, id: id, label: workspace.label)
            }
        }
    }

    @ViewBuilder
    private func tabMenu(_ server: HerdrServer, _ tab: HerdrTab) -> some View {
        if server.features.rename {
            Button("Rename…") {
                model.dialog = .rename(server: server, target: .tab, id: tab.tab_id, current: tab.label)
            }
        }
        if server.features.close_group {
            Button("Close tab…", role: .destructive) {
                model.dialog = .close(server: server, target: .tab, id: tab.tab_id, label: tab.label)
            }
        }
    }
}
