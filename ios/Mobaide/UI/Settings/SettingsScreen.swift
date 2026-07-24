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
    }

    var body: some View {
        Form {
            Section {
                LabeledContent("Signed in as", value: model.email)
            }
            Section("Management") {
                NavigationLink("My Agents", value: Route.agentList)
                NavigationLink("Templates", value: Route.templates)
                NavigationLink("Devices", value: Route.devices)
            }
            Section {
                Picker("Mode", selection: displayModeBinding) {
                    Text("Full Screen").tag(TerminalDisplayMode.full)
                    Text("Static").tag(TerminalDisplayMode.static)
                }
                .pickerStyle(.segmented)
                Stepper(
                    "Columns: \(displayStore.staticColumns)",
                    value: staticColumnsBinding,
                    in: TerminalDisplayDefaults.minColumns...TerminalDisplayDefaults.maxColumns,
                    step: TerminalDisplayDefaults.columnsStep
                )
                .disabled(displayStore.mode != .static)
            } header: {
                Text("Terminal display")
            } footer: {
                Text("Full Screen reflows columns to fit the screen. Static keeps a fixed column count and pans horizontally.")
            }
            Section("Support") {
                NavigationLink("Help & Support", value: Route.support)
                Button("Privacy policy") { model.openPrivacyPolicy() }
            }
            Section {
                Button("Log out") { Task { await model.logout() } }
            }
            Section {
                Button("Delete account", role: .destructive) { deleteModel.start() }
            } header: {
                Text("Danger zone")
            } footer: {
                Text("Permanently deletes your account and all associated data. In-app account deletion is required by the App Store.")
            }
        }
        .navigationTitle("Settings")
        .sheet(isPresented: deletePresented) {
            DeleteAccountSheet(model: deleteModel)
        }
        .sheet(isPresented: safariPresented) {
            if let url = model.safariURL {
                SafariView(url: url)
            }
        }
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
