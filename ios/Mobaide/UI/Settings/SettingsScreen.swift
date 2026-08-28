import SwiftUI

/// Settings (Android `SettingsScreen`): account header, the terminal display
/// mode, management nav rows, support links, logout, and the danger-zone
/// delete-account entry.
struct SettingsScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            SettingsContent(container: container)
        }
    }
}

private struct SettingsContent: View {
    @State private var model: SettingsModel
    @State private var deleteModel: DeleteAccountModel
    private let displayStore: TerminalDisplayStore
    private let themeStore: ThemeStore

    @Environment(\.mobaideColors) private var colors

    init(container: AppContainer) {
        _model = State(initialValue: SettingsModel(
            authRepository: container.authRepository,
            onSignOut: { [pushRegistration = container.pushRegistrationCoordinator] in
                pushRegistration.handleSignOut()
            }
        ))
        _deleteModel = State(initialValue: DeleteAccountModel(
            authRepository: container.authRepository,
            signInService: container.signInService,
            email: container.authRepository.getUserEmail() ?? ""
        ))
        displayStore = container.terminalDisplayStore
        themeStore = container.themeStore
    }

    var body: some View {
        List {
            Section {
                LabeledContent("Signed in as", value: model.email)
            }
            .pixelFormSection()
            Section(header: PixelSectionHeader("Management")) {
                NavigationLink("My Agents", value: Route.agentList)
                NavigationLink("Templates", value: Route.templates)
                NavigationLink("Devices", value: Route.devices)
            }
            .pixelFormSection()
            // The web `ThemePicker`: direct selection, never a cycle button —
            // the checked segment moving is the only feedback a colour-neutral
            // change (Auto under a matching OS) can give.
            Section {
                PixelSegmented(
                    ThemePreference.allCases.map { ($0, $0.label) },
                    selection: themeBinding,
                    accessibilityLabel: "Appearance"
                )
                .padding(.vertical, 4)
            } header: {
                PixelSectionHeader("Appearance")
            } footer: {
                PixelSectionFooter("Auto follows the system appearance.")
            }
            .pixelFormSection()
            Section {
                PixelSegmented(
                    [(TerminalDisplayMode.full, "Full Screen"), (TerminalDisplayMode.static, "Static")],
                    selection: displayModeBinding,
                    accessibilityLabel: "Terminal display mode"
                )
                .padding(.vertical, 4)
                Stepper(
                    "Columns: \(displayStore.staticColumns)",
                    value: staticColumnsBinding,
                    in: TerminalDisplayDefaults.minColumns...TerminalDisplayDefaults.maxColumns,
                    step: TerminalDisplayDefaults.columnsStep
                )
                .disabled(displayStore.mode != .static)
            } header: {
                PixelSectionHeader("Terminal display")
            } footer: {
                PixelSectionFooter("Full Screen reflows columns to fit the screen. Static keeps a fixed column count and pans horizontally.")
            }
            .pixelFormSection()
            Section(header: PixelSectionHeader("Support")) {
                NavigationLink("Help & Support", value: Route.support)
                Button("Privacy policy") { model.openPrivacyPolicy() }
            }
            .pixelFormSection()
            Section {
                Button("Log out") { Task { await model.logout() } }
                    .buttonStyle(.pixelBlock)
            }
            .listRowBackground(Color.clear)
            Section {
                Button("Delete account", role: .destructive) { deleteModel.start() }
                    .buttonStyle(.pixelDangerBlock)
            } header: {
                Text("Danger zone")
                    .chromeText()
                    .foregroundStyle(colors.danger)
            } footer: {
                PixelSectionFooter("Permanently deletes your account and all associated data. In-app account deletion is required by the App Store.")
            }
            .listRowBackground(Color.clear)
        }
        .pixelForm()
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: deletePresented) {
            DeleteAccountSheet(model: deleteModel)
        }
        .sheet(isPresented: safariPresented) {
            if let url = model.safariURL {
                SafariView(url: url)
            }
        }
    }

    private var themeBinding: Binding<ThemePreference> {
        Binding(get: { themeStore.preference }, set: { themeStore.setPreference($0) })
    }

    private var displayModeBinding: Binding<TerminalDisplayMode> {
        Binding(get: { displayStore.mode }, set: { displayStore.setMode($0) })
    }

    private var staticColumnsBinding: Binding<Int> {
        Binding(get: { displayStore.staticColumns }, set: { displayStore.setStaticColumns($0) })
    }

    private var deletePresented: Binding<Bool> {
        Binding(get: { deleteModel.isVisible }, set: { if !$0 { deleteModel.dismiss() } })
    }

    private var safariPresented: Binding<Bool> {
        Binding(get: { model.safariURL != nil }, set: { if !$0 { model.safariURL = nil } })
    }
}
