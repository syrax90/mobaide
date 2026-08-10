import SwiftUI

/// Agents list (Android `AgentListScreen`) — the authenticated root screen.
/// Scan-primary FAB for QR pairing; tap a card to connect; overflow menu for
/// manual add, install commands, Settings, and Templates / Devices navigation.
/// Hosts the account-deletion grace banner (Android landing-screen parity).
struct AgentListScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            AgentListContent(container: container)
        }
    }
}

private struct AgentListContent: View {
    @State private var model: AgentListModel
    @State private var toast: ToastMessage?

    @Environment(Router.self) private var router

    let container: AppContainer

    init(container: AppContainer) {
        self.container = container
        _model = State(initialValue: AgentListModel(
            agentRepository: container.agentRepository,
            authRepository: container.authRepository,
            trustKeyPinning: container.trustKeyPinning,
            deviceIdentityStore: container.deviceIdentityStore,
            tokenStore: container.tokenStore,
            router: container.router
        ))
    }

    var body: some View {
        content
            .navigationTitle("Agents")
            .safeAreaInset(edge: .top, spacing: 0) {
                GraceBannerSection(authRepository: container.authRepository)
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        // Above the install commands because this is the path
                        // that works when the agent's screen is not somewhere a
                        // camera can see it — an SSH session, a VM console, a
                        // machine in another room.
                        Button { model.startDeviceCodePairing() } label: {
                            Label("Pair with a code", systemImage: "keyboard")
                        }
                        Button { model.startAdd() } label: {
                            Label("Add agent manually", systemImage: "plus")
                        }
                        Button { model.showInstallSheet = true } label: {
                            Label("Show install commands", systemImage: "terminal")
                        }
                        Divider()
                        Button { router.navigate(to: .templates) } label: {
                            Label("Manage templates", systemImage: "doc.plaintext")
                        }
                        Button { router.navigate(to: .devices) } label: {
                            Label("Manage devices", systemImage: "iphone")
                        }
                        Button { router.navigate(to: .connections) } label: {
                            Label("Manage connections", systemImage: "rectangle.2.swap")
                        }
                        Button { router.navigate(to: .projects) } label: {
                            Label("Projects", systemImage: "folder")
                        }
                        Divider()
                        Button { router.navigate(to: .settings) } label: {
                            Label("Settings", systemImage: "gearshape")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .overlay(alignment: .bottomTrailing) { scanFab }
            .overlay { busyOverlay }
            .refreshable { await model.refresh() }
            .task { await model.observeAgents() }
            .task { await model.refresh() }
            .sheet(isPresented: $model.pairingSheetPresented, onDismiss: { model.resetPairing() }) {
                PairingSheet(model: model)
            }
            .sheet(
                isPresented: $model.deviceCodeSheetPresented,
                onDismiss: { model.resetDeviceCodePairing() }
            ) {
                DeviceCodePairingSheet(model: model)
            }
            .sheet(item: $model.credentials) { creds in
                AgentCredentialsSheet(credentials: creds) { model.credentials = nil }
            }
            .sheet(isPresented: $model.showInstallSheet) {
                InstallCommandsSheet { model.showInstallSheet = false }
            }
            .alert("Add agent", isPresented: $model.showAddInput) {
                TextField("Agent name", text: $model.addName)
                Button("Create") { model.submitAdd() }
                Button("Cancel", role: .cancel) { }
            } message: {
                Text("Create an agent to get a one-time token. Prefer QR pairing (Scan) when you can.")
            }
            .alert(
                "Re-register agent",
                isPresented: presenting(\.reregisterConfirm),
                presenting: model.reregisterConfirm
            ) { _ in
                Button("Re-register", role: .destructive) { model.confirmReRegister() }
                Button("Cancel", role: .cancel) { model.reregisterConfirm = nil }
            } message: { agent in
                Text("This generates new credentials for “\(agent.name)” and invalidates the existing token immediately.")
            }
            .alert(
                "Delete agent",
                isPresented: presenting(\.deleteConfirm),
                presenting: model.deleteConfirm
            ) { _ in
                Button("Delete", role: .destructive) { model.confirmDelete() }
                Button("Cancel", role: .cancel) { model.deleteConfirm = nil }
            } message: { agent in
                Text("Permanently remove “\(agent.name)” and all associated data.")
            }
            .toast($toast)
            .onChange(of: model.message) { _, newValue in
                if let newValue {
                    toast = ToastMessage(text: newValue)
                    model.clearMessage()
                }
            }
    }

    @ViewBuilder
    private var content: some View {
        if model.agents.isEmpty {
            ContentUnavailableView {
                Label("No agents yet", systemImage: "server.rack")
            } description: {
                Text("Scan the pairing QR shown on your agent host, or add one manually.")
            } actions: {
                Button("Scan pairing QR") { model.startScan() }
                    .buttonStyle(.borderedProminent)
                Button("Show install commands") { model.showInstallSheet = true }
            }
        } else {
            List {
                ForEach(model.agents, id: \.agent_id) { agent in
                    HStack(spacing: 8) {
                        Button { model.connect(agent) } label: {
                            AgentRow(agent: agent, isConnected: agent.agent_id == model.connectedAgentId)
                        }
                        .buttonStyle(.plain)
                        Spacer(minLength: 0)
                        Menu {
                            Button { model.startReRegister(agent) } label: {
                                Label("Re-register", systemImage: "arrow.triangle.2.circlepath")
                            }
                            Button(role: .destructive) { model.startDelete(agent) } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        } label: {
                            Image(systemName: "ellipsis")
                                .padding(8)
                                .contentShape(Rectangle())
                        }
                    }
                }
            }
            .listStyle(.plain)
        }
    }

    private var scanFab: some View {
        Button { model.startScan() } label: {
            Image(systemName: "qrcode.viewfinder")
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 56, height: 56)
                .background(Color.accentColor, in: Circle())
                .shadow(radius: 4)
        }
        .padding(20)
        .accessibilityLabel("Scan pairing QR")
    }

    @ViewBuilder
    private var busyOverlay: some View {
        if model.isAddBusy || model.isReRegisterBusy {
            ProgressView()
                .controlSize(.large)
                .padding(28)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
        }
    }

    /// A `Bool` binding derived from an optional model field: false clears it.
    private func presenting(_ keyPath: ReferenceWritableKeyPath<AgentListModel, AgentSummaryResponse?>) -> Binding<Bool> {
        Binding(
            get: { model[keyPath: keyPath] != nil },
            set: { if !$0 { model[keyPath: keyPath] = nil } }
        )
    }
}

private struct AgentRow: View {
    let agent: AgentSummaryResponse
    let isConnected: Bool

    var body: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(agent.online ? MobaideColors.stateRunning : MobaideColors.stateStopped)
                .frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 8) {
                    Text(agent.name).font(.headline)
                    if isConnected {
                        Text("Connected")
                            .font(MobaideTypography.labelSmall)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.accentColor.opacity(0.15), in: Capsule())
                    }
                }
                Text(agent.online ? "Online" : "Offline")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }
}

// MARK: - Pairing sheet (scanner → flow, single presentation)

private struct PairingSheet: View {
    @Bindable var model: AgentListModel

    var body: some View {
        Group {
            switch model.qrPairing {
            case .idle:
                QrScannerView(
                    onScan: { model.onScanResult($0) },
                    onCancel: { model.onScanCancelled() }
                )
            default:
                NavigationStack {
                    PairingFlowView(model: model)
                        .padding()
                        .navigationBarTitleDisplayMode(.inline)
                }
                .presentationDetents([.medium])
            }
        }
        .interactiveDismissDisabled(model.qrPairing.isClaiming)
    }
}

private struct PairingFlowView: View {
    @Bindable var model: AgentListModel

    var body: some View {
        switch model.qrPairing {
        case .confirming(let payload):
            confirming(payload)
        case .claiming:
            progress("Claiming the pairing code…")
        case .waitingForOnline(_, let name, let elapsed, _):
            waiting(name: name, elapsed: elapsed)
        case .success(_, let name):
            success(name: name)
        case .error(let message):
            failure(message)
        case .idle:
            EmptyView()
        }
    }

    private func confirming(_ payload: ParsedQrPairingPayload) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "qrcode")
                .font(.system(size: 44))
                .foregroundStyle(.tint)
            Text("Pair this agent?").font(.title3.bold())
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Relay", value: payload.relayUrl)
                LabeledContent("Pairing code", value: "\(payload.nonce.prefix(8))…")
                LabeledContent("Expires (unix)", value: String(payload.expUnix))
            }
            .font(.callout)
            HStack {
                Button("Cancel", role: .cancel) { model.dismissPairing() }
                Button("Pair") { model.confirmClaim() }
                    .buttonStyle(.borderedProminent)
            }
            .padding(.top, 4)
        }
    }

    private func progress(_ text: String) -> some View {
        VStack(spacing: 16) {
            ProgressView()
            Text(text).font(.headline)
        }
    }

    private func waiting(name: String, elapsed: Int) -> some View {
        VStack(spacing: 16) {
            ProgressView()
            Text("Waiting for ‘\(name)’ to come online…").font(.headline)
            Text("Elapsed \(elapsed)s / \(AgentListModel.waitForOnlineTimeoutSeconds)s")
                .foregroundStyle(.secondary)
            Button("Stop waiting", role: .cancel) { model.dismissPairing() }
        }
    }

    private func success(name: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 44))
                .foregroundStyle(MobaideColors.stateRunning)
            Text("‘\(name)’ paired and online").font(.title3.bold())
            Button("OK") { model.dismissPairing() }
                .buttonStyle(.borderedProminent)
        }
    }

    private func failure(_ message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 40))
                .foregroundStyle(MobaideColors.warningYellow)
            Text("Pairing failed").font(.title3.bold())
            Text(message)
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Button("Close") { model.dismissPairing() }
                .buttonStyle(.borderedProminent)
        }
    }
}

/// The reversed direction.
///
/// There is no waiting-for-online case here — once the confirm lands the flow
/// hands off to `PairingSheet`.
private struct DeviceCodePairingSheet: View {
    @Bindable var model: AgentListModel

    var body: some View {
        NavigationStack {
            content
                .padding()
                .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium])
        .interactiveDismissDisabled(model.deviceCodePairing.isClaiming)
    }

    @ViewBuilder
    private var content: some View {
        switch model.deviceCodePairing {
        case .idle:
            EmptyView()
        case .minting:
            VStack(spacing: 16) {
                ProgressView()
                Text("Getting a code…").font(.headline)
            }
        case .waitingForHost(_, let code, let command):
            waitingForHost(code: code, command: command)
        case .confirming(_, let hostname, let os, let repairAgentName, let blocked):
            confirming(
                hostname: hostname,
                os: os,
                repairAgentName: repairAgentName,
                blocked: blocked
            )
        case .claiming:
            VStack(spacing: 16) {
                ProgressView()
                Text("Pairing…").font(.headline)
            }
        case .error(let message):
            VStack(spacing: 16) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 40))
                    .foregroundStyle(MobaideColors.warningYellow)
                Text("Pairing failed").font(.title3.bold())
                Text(message)
                    .font(.callout)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                Button("Close") { model.dismissDeviceCodePairing() }
                    .buttonStyle(.borderedProminent)
            }
        }
    }

    private func waitingForHost(code: String, command: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "keyboard")
                .font(.system(size: 40))
                .foregroundStyle(.tint)
            Text("On the computer you want to pair, run:").font(.headline)
            CopyableCodeBlock(label: "Pairing command", code: command)
            Text(
                "The code is \(code) if you would rather type it yourself. The agent "
                + "will name this account and ask you to confirm before it pairs — only "
                + "enter it on a machine you own."
            )
            .font(.footnote)
            .multilineTextAlignment(.center)
            .foregroundStyle(.secondary)
            ProgressView()
            Text("Waiting for a machine to enter the code…")
                .font(.footnote)
                .foregroundStyle(.secondary)
            Button("Cancel", role: .cancel) { model.dismissDeviceCodePairing() }
        }
    }

    private func confirming(
        hostname: String,
        os: String?,
        repairAgentName: String?,
        blocked: DeviceCodeBlockedReason?
    ) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "desktopcomputer")
                .font(.system(size: 40))
                .foregroundStyle(.tint)
            Text(
                repairAgentName.map { "Move ‘\($0)’ to this machine?" }
                    ?? "Is this your machine?"
            )
            .font(.title3.bold())
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Hostname", value: hostname)
                if let os {
                    LabeledContent("System", value: os)
                }
            }
            .font(.callout)
            // The name above is whatever the machine that entered the code
            // reported, over an endpoint that authenticates nobody — it helps a
            // user recognise their own host and proves nothing by itself.
            Text(
                "A machine calling itself \(hostname) entered your code. If that is not "
                + "the computer you just typed it into, do not continue."
            )
            .font(.footnote)
            .multilineTextAlignment(.center)
            .foregroundStyle(.secondary)
            if let repairAgentName {
                Text(
                    "That machine reported it is already ‘\(repairAgentName)’. "
                    + "Continuing rotates that agent’s credentials onto it — if the "
                    + "real ‘\(repairAgentName)’ is a different computer, that one "
                    + "goes offline."
                )
                .font(.footnote)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            }
            if let blocked {
                Text(blocked.message)
                    .font(.footnote)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(MobaideColors.warningYellow)
            }
            HStack {
                Button("Cancel", role: .cancel) { model.dismissDeviceCodePairing() }
                Button("Pair this machine") { model.confirmDeviceCode() }
                    .buttonStyle(.borderedProminent)
                    .disabled(blocked != nil)
            }
            .padding(.top, 4)
        }
    }
}
