import Observation
import SwiftUI

/// Mirror of Android `ConnectionsScreen`: saved connection-profile switcher.
/// Switching disconnects the current agent, loads the stored profile's
/// credentials into `TokenStore`, and reconnects (the auth-state-bound event
/// buses tear down and rebuild themselves on the state transitions).
struct ConnectionsScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            ConnectionsContent(container: container)
        }
    }
}

private struct ConnectionsContent: View {
    @State private var model: ConnectionsModel

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors

    init(container: AppContainer) {
        _model = State(initialValue: ConnectionsModel(
            connectionStore: container.connectionStore,
            tokenStore: container.tokenStore,
            sessionManager: container.sessionManager
        ))
    }

    var body: some View {
        Group {
            if model.profiles.isEmpty {
                PixelEmptyState("No saved connections", systemImage: "rectangle.2.swap")
                    .padding(16)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    .pixelScreenBackground()
            } else {
                List {
                    ForEach(model.profiles, id: \.id) { profile in
                        profileRow(profile)
                            .pixelListRow()
                    }
                }
                .pixelList()
            }
        }
        .navigationTitle("Connections")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                // Android parity: "add connection" routes to the login
                // surface; QR pairing (iOS-06) will take over this entry.
                Button {
                    router.navigate(to: .login)
                } label: {
                    Image(systemName: "plus")
                }
                .accessibilityLabel("Add connection")
            }
        }
        .onAppear { model.load() }
    }

    private func profileRow(_ profile: ConnectionStore.ConnectionProfile) -> some View {
        let isActive = profile.id == model.activeProfileId
        return HStack(spacing: 12) {
            Image(systemName: profile.mode == "relay" ? "cloud" : "network")
                .foregroundStyle(colors.textMuted)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(profile.label)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(colors.text)
                    if isActive {
                        PixelChip("Active", .filled)
                    }
                }
                Text(subtitle(for: profile))
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            }
            Spacer()
            if !isActive {
                Button {
                    model.removeProfile(id: profile.id)
                } label: {
                    Image(systemName: "trash")
                        .foregroundStyle(colors.danger)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Delete \(profile.label)")
            }
        }
        .pixelCard(selected: isActive)
        .contentShape(Rectangle())
        .onTapGesture {
            guard !isActive else { return }
            Task {
                await model.switchProfile(id: profile.id)
                // Deterministic landing spot: Android's conflated StateFlow
                // usually skips the intermediate userOnly emission, but the
                // iOS buffered streams may clear the path mid-switch — an
                // explicit pop lands the user on the new profile's agent list.
                router.popToRoot()
            }
        }
    }

    private func subtitle(for profile: ConnectionStore.ConnectionProfile) -> String {
        profile.mode == "relay"
            ? "Relay: \(profile.relayServerUrl ?? "unknown")"
            : "Direct: \(profile.agentUrl ?? "unknown")"
    }
}

@MainActor
@Observable
final class ConnectionsModel {
    private(set) var profiles: [ConnectionStore.ConnectionProfile] = []
    private(set) var activeProfileId: String?

    private let connectionStore: ConnectionStore
    private let tokenStore: TokenStore
    private let sessionManager: SessionManager

    init(
        connectionStore: ConnectionStore,
        tokenStore: TokenStore,
        sessionManager: SessionManager
    ) {
        self.connectionStore = connectionStore
        self.tokenStore = tokenStore
        self.sessionManager = sessionManager
        load()
    }

    func load() {
        profiles = connectionStore.listProfiles()
        activeProfileId = connectionStore.getActiveProfileId()
    }

    /// Android `switchProfile` parity, minus the explicit bus stop/start
    /// (iOS buses are bound to the auth state): disconnect the agent only —
    /// user identity stays — then activate and reconnect. A profile with
    /// incomplete credentials is left inactive instead of being marked
    /// connected with an empty `TokenStore`.
    func switchProfile(id: String) async {
        await sessionManager.agentDisconnect()
        connectionStore.activateProfile(id: id, tokenStore: tokenStore)
        if tokenStore.isLoggedIn() {
            await sessionManager.markAgentConnected()
        }
        load()
    }

    func removeProfile(id: String) {
        connectionStore.removeProfile(id: id)
        load()
    }
}
