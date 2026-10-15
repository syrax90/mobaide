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
    @Environment(AuthStateModel.self) private var authStateModel
    @Environment(\.mobaideColors) private var colors

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
            .navigationBarTitleDisplayMode(.inline)
            .safeAreaInset(edge: .top, spacing: 0) {
                GraceBannerSection(authRepository: container.authRepository)
            }
            .toolbar {
                // The web header's brand mark: the authenticated root carries
                // the wordmark; pushed screens keep their text titles.
                ToolbarItem(placement: .principal) {
                    PixelWordmark(width: 104)
                }
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
            // Both legs of the agent switch a notification deep link can drive
            // pass through here, so the connected badge follows it.
            .onChange(of: authStateModel.state, initial: true) { _, _ in
                model.syncConnectedAgent()
            }
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
            ) { agent in
                // An alert cannot host a toggle, so the web/Android uninstall
                // checkbox becomes two buttons; uninstalling comes first as
                // the default choice there.
                if agent.supports_uninstall {
                    Button("Delete & Uninstall", role: .destructive) { model.confirmDelete(uninstall: true) }
                    Button("Delete Only", role: .destructive) { model.confirmDelete() }
                } else {
                    Button("Delete", role: .destructive) { model.confirmDelete() }
                }
                Button("Cancel", role: .cancel) { model.deleteConfirm = nil }
            } message: { agent in
                Text(Self.deleteMessage(for: agent))
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
            ScrollView {
                EmptyAgentsState(
                    onShowInstall: { model.showInstallSheet = true },
                    onScan: { model.startScan() },
                    onPairWithCode: { model.startDeviceCodePairing() },
                    onAddManually: { model.startAdd() }
                )
                .padding(16)
                // Clears the 56pt scan FAB so the last row stays tappable.
                .padding(.bottom, 88)
            }
            .pixelScreenBackground()
        } else {
            List {
                ForEach(model.agents, id: \.agent_id) { agent in
                    let isConnected = agent.agent_id == model.connectedAgentId
                    HStack(spacing: 8) {
                        Button { model.connect(agent) } label: {
                            AgentRow(agent: agent, isConnected: isConnected)
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
                                .foregroundStyle(colors.accent)
                                .frame(width: 44, height: 44)
                                .contentShape(Rectangle())
                        }
                    }
                    .pixelCard(selected: isConnected)
                    .pixelListRow()
                }
            }
            .pixelList()
        }
    }

    /// The web has no FAB; this keeps Android's scan-primary affordance in the
    /// pixel button grammar — a `brand` square on its hard shadow.
    private var scanFab: some View {
        Button { model.startScan() } label: {
            Image(systemName: "qrcode.viewfinder")
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 56, height: 56)
                .background(MobaideColorScheme.brand)
                .pixelShadow(MobaideColorScheme.btnShadow)
        }
        .buttonStyle(.plain)
        .padding(20)
        .accessibilityLabel("Scan pairing QR")
    }

    @ViewBuilder
    private var busyOverlay: some View {
        if model.isAddBusy || model.isReRegisterBusy {
            PixelBusyOverlay()
        }
    }

    /// A `Bool` binding derived from an optional model field: false clears it.
    private func presenting(_ keyPath: ReferenceWritableKeyPath<AgentListModel, AgentSummaryResponse?>) -> Binding<Bool> {
        Binding(
            get: { model[keyPath: keyPath] != nil },
            set: { if !$0 { model[keyPath: keyPath] = nil } }
        )
    }

    private static func deleteMessage(for agent: AgentSummaryResponse) -> String {
        let base = "Permanently remove “\(agent.name)” and all associated data."
        guard agent.supports_uninstall else { return base }
        let when = agent.online
            ? "right away"
            : "the next time it connects (within 30 days), as it is offline"
        return base + " Delete & Uninstall also makes the agent stop and delete itself from its computer \(when)."
    }
}

// MARK: - Empty state (Android `EmptyAgentsState`)

/// Nothing is scannable until `mobaide-agent` runs on a host, so the two
/// actions are an ordered pair — install, then pair — rather than the flat
/// button row this screen used to show, which led with the scan.
private struct EmptyAgentsState: View {
    let onShowInstall: () -> Void
    let onScan: () -> Void
    let onPairWithCode: () -> Void
    let onAddManually: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        PixelEmptyState(
            "No agents yet",
            systemImage: "qrcode.viewfinder",
            description: "Install mobaide-agent on a host machine. The agent will display "
                + "a pairing QR after install — scan it here to finish setup."
        ) {
            VStack(alignment: .leading, spacing: 20) {
                step(1, "Install on your host") {
                    Button(action: onShowInstall) {
                        Label("Show install commands", systemImage: "terminal")
                    }
                    .buttonStyle(.pixelPrimaryBlock)
                }
                step(2, "Scan the pairing QR shown after install") {
                    Button(action: onScan) {
                        Label("Scan QR code", systemImage: "qrcode.viewfinder")
                    }
                    .buttonStyle(.pixelBlock)
                }
                VStack(spacing: 0) {
                    fallback("Pair with a code instead", action: onPairWithCode)
                    fallback("Add manually instead", action: onAddManually)
                }
                .frame(maxWidth: .infinity)
            }
            .padding(.top, 8)
        }
    }

    private func step<Content: View>(
        _ number: Int,
        _ title: String,
        @ViewBuilder button: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            PixelNumberedStep(number: number, title: title)
            button()
        }
    }

    /// The two escape hatches stay plain text: a third and fourth ringed pixel
    /// button would read as peers of the numbered steps.
    private func fallback(_ title: String, action: @escaping () -> Void) -> some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(.footnote)
            .foregroundStyle(colors.accent)
            // Padding, not a fixed minHeight: it is a 44pt target at the
            // default size and keeps the two links apart as the text grows.
            .padding(.vertical, 14)
            .contentShape(Rectangle())
    }
}

/// The ordinal as a filled `brand` square — the pixel kit has no circles.
private struct PixelNumberedStep: View {
    let number: Int
    let title: String

    /// Scaled, not fixed: the ordinal is Dynamic Type text, and a 20pt box
    /// clips it outright at the accessibility sizes.
    @ScaledMetric(relativeTo: .caption) private var side: CGFloat = 20

    var body: some View {
        // Baseline, not center: step 2's label wraps, and centering would
        // float the square between its two lines.
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text("\(number)")
                .font(MobaideTypography.chromeSmall)
                .foregroundStyle(.white)
                .frame(minWidth: side, minHeight: side)
                .background(MobaideColorScheme.brand)
            Text(title)
                .sectionHeading()
                .multilineTextAlignment(.leading)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct AgentRow: View {
    let agent: AgentSummaryResponse
    let isConnected: Bool

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 12) {
            PixelStatusSquare(tone: agent.online ? .ok : .muted)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(agent.name)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(colors.text)
                    if isConnected {
                        PixelChip("Connected", .filled)
                    }
                }
                Text(agent.online ? "Online" : "Offline")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
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
                .pixelSheet()
                .presentationDetents([.medium])
            }
        }
        .interactiveDismissDisabled(model.qrPairing.isClaiming)
    }
}

private struct PairingFlowView: View {
    @Bindable var model: AgentListModel

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        switch model.qrPairing {
        case .confirming(let payload):
            confirming(payload)
        case .claiming:
            progress("Claiming the pairing code…")
        case .waitingForOnline(_, let name, let elapsed, _):
            waiting(name: name, elapsed: elapsed)
        case .waitTimedOut(_, let name, let ws):
            notOnlineYet(name: name, wsDeliveredAtClaim: ws)
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
                .foregroundStyle(colors.accent)
            Text("Pair this agent?")
                .font(MobaideTypography.dialogTitle)
                .foregroundStyle(colors.accent)
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Relay", value: payload.relayUrl)
                LabeledContent("Pairing code", value: "\(payload.nonce.prefix(8))…")
                LabeledContent("Expires (unix)", value: String(payload.expUnix))
            }
            .font(.callout)
            .foregroundStyle(colors.text)
            HStack(spacing: 12) {
                Button("Cancel", role: .cancel) { model.dismissPairing() }
                    .buttonStyle(.pixel)
                Button("Pair") { model.confirmClaim() }
                    .buttonStyle(.pixelPrimary)
            }
            .padding(.top, 4)
        }
    }

    private func progress(_ text: String) -> some View {
        VStack(spacing: 16) {
            PixelLoadingBlock(width: 10, height: 18)
            Text(text)
                .font(.headline)
                .foregroundStyle(colors.text)
        }
    }

    private func waiting(name: String, elapsed: Int) -> some View {
        VStack(spacing: 16) {
            PixelLoadingBlock(width: 10, height: 18)
            Text("Waiting for ‘\(name)’ to come online…")
                .font(.headline)
                .foregroundStyle(colors.text)
            Text("Elapsed \(elapsed)s / \(AgentListModel.waitForOnlineTimeoutSeconds)s")
                .foregroundStyle(colors.textMuted)
                .monospacedDigit()
            Button("Stop waiting", role: .cancel) { model.dismissPairing() }
                .buttonStyle(.pixel)
        }
    }

    private func notOnlineYet(name: String, wsDeliveredAtClaim: Bool?) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "clock.badge.exclamationmark")
                .font(.system(size: 40))
                .foregroundStyle(colors.warning)
            Text("Agent not online yet")
                .font(MobaideTypography.dialogTitle)
                .foregroundStyle(colors.accent)
            Text(AgentListModel.waitTimeoutMessage(friendlyName: name, wsDeliveredAtClaim: wsDeliveredAtClaim))
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(colors.textMuted)
            HStack(spacing: 12) {
                Button("Close", role: .cancel) { model.dismissPairing() }
                    .buttonStyle(.pixel)
                Button("Keep waiting") { model.keepWaiting() }
                    .buttonStyle(.pixelPrimary)
            }
            .padding(.top, 4)
        }
    }

    private func success(name: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.square.fill")
                .font(.system(size: 44))
                .foregroundStyle(colors.ok)
            Text("‘\(name)’ paired and online")
                .font(MobaideTypography.dialogTitle)
                .foregroundStyle(colors.accent)
                .multilineTextAlignment(.center)
            Button("OK") { model.dismissPairing() }
                .buttonStyle(.pixelPrimary)
        }
    }

    private func failure(_ message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 40))
                .foregroundStyle(colors.warning)
            Text("Pairing failed")
                .font(MobaideTypography.dialogTitle)
                .foregroundStyle(colors.accent)
            Text(message)
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(colors.textMuted)
            Button("Close") { model.dismissPairing() }
                .buttonStyle(.pixelPrimary)
        }
    }
}

/// The reversed direction.
///
/// There is no waiting-for-online case here — once the confirm lands the flow
/// hands off to `PairingSheet`.
private struct DeviceCodePairingSheet: View {
    @Bindable var model: AgentListModel

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        NavigationStack {
            content
                .padding()
                .navigationBarTitleDisplayMode(.inline)
        }
        .pixelSheet()
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
                PixelLoadingBlock(width: 10, height: 18)
                Text("Getting a code…")
                    .font(.headline)
                    .foregroundStyle(colors.text)
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
                PixelLoadingBlock(width: 10, height: 18)
                Text("Pairing…")
                    .font(.headline)
                    .foregroundStyle(colors.text)
            }
        case .error(let message):
            VStack(spacing: 16) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 40))
                    .foregroundStyle(colors.warning)
                Text("Pairing failed")
                    .font(MobaideTypography.dialogTitle)
                    .foregroundStyle(colors.accent)
                Text(message)
                    .font(.callout)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(colors.textMuted)
                Button("Close") { model.dismissDeviceCodePairing() }
                    .buttonStyle(.pixelPrimary)
            }
        }
    }

    private func waitingForHost(code: String, command: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "keyboard")
                .font(.system(size: 40))
                .foregroundStyle(colors.accent)
            Text("On the computer you want to pair, run:")
                .font(.headline)
                .foregroundStyle(colors.text)
            CopyableCodeBlock(label: "Pairing command", code: command)
            Text(
                "The code is \(code) if you would rather type it yourself. The agent "
                + "will name this account and ask you to confirm before it pairs — only "
                + "enter it on a machine you own."
            )
            .font(.footnote)
            .multilineTextAlignment(.center)
            .foregroundStyle(colors.textMuted)
            PixelLoadingBlock()
            Text("Waiting for a machine to enter the code…")
                .font(.footnote)
                .foregroundStyle(colors.textMuted)
            Button("Cancel", role: .cancel) { model.dismissDeviceCodePairing() }
                .buttonStyle(.pixel)
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
                .foregroundStyle(colors.accent)
            Text(
                repairAgentName.map { "Move ‘\($0)’ to this machine?" }
                    ?? "Is this your machine?"
            )
            .font(MobaideTypography.dialogTitle)
            .foregroundStyle(colors.accent)
            .multilineTextAlignment(.center)
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Hostname", value: hostname)
                if let os {
                    LabeledContent("System", value: os)
                }
            }
            .font(.callout)
            .foregroundStyle(colors.text)
            // The name above is whatever the machine that entered the code
            // reported, over an endpoint that authenticates nobody — it helps a
            // user recognise their own host and proves nothing by itself.
            Text(
                "A machine calling itself \(hostname) entered your code. If that is not "
                + "the computer you just typed it into, do not continue."
            )
            .font(.footnote)
            .multilineTextAlignment(.center)
            .foregroundStyle(colors.textMuted)
            if let repairAgentName {
                Text(
                    "That machine reported it is already ‘\(repairAgentName)’. "
                    + "Continuing rotates that agent’s credentials onto it — if the "
                    + "real ‘\(repairAgentName)’ is a different computer, that one "
                    + "goes offline."
                )
                .font(.footnote)
                .multilineTextAlignment(.center)
                .foregroundStyle(colors.textMuted)
            }
            if let blocked {
                Text(blocked.message)
                    .font(.footnote)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(colors.warning)
            }
            HStack(spacing: 12) {
                Button("Cancel", role: .cancel) { model.dismissDeviceCodePairing() }
                    .buttonStyle(.pixel)
                Button("Pair this machine") { model.confirmDeviceCode() }
                    .buttonStyle(.pixelPrimary)
                    .disabled(blocked != nil)
            }
            .padding(.top, 4)
        }
    }
}
