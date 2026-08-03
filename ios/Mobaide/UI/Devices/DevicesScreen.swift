import SwiftUI

/// Device list (Android `DevicesScreen`): list + revoke with a per-row
/// confirmation, optimistic removal, and a badge on the current device.
struct DevicesScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            DevicesContent(container: container)
        }
    }
}

private struct DevicesContent: View {
    @State private var model: DevicesModel
    @State private var toast: ToastMessage?

    init(container: AppContainer) {
        _model = State(initialValue: DevicesModel(
            deviceRepository: container.deviceRepository,
            deviceIdentityStore: container.deviceIdentityStore
        ))
    }

    var body: some View {
        content
            .navigationTitle("My devices")
            .refreshable { await model.refresh() }
            .task { await model.refresh() }
            .alert(
                "Revoke device?",
                isPresented: Binding(
                    get: { model.revokeTarget != nil },
                    set: { if !$0 { model.revokeTarget = nil } }
                ),
                presenting: model.revokeTarget
            ) { device in
                Button("Revoke", role: .destructive) { model.confirmRevoke(device) }
                Button("Cancel", role: .cancel) { model.revokeTarget = nil }
            } message: { device in
                if model.isCurrent(device) {
                    Text("Revoking this device signs you out on the next token refresh. You can sign back in afterwards. Every agent paired with it is signed out too.")
                } else {
                    Text("\(device.name ?? device.device_id) will be signed out on its next token refresh. Every agent paired with it is signed out too.")
                }
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
        if model.isEmpty {
            if model.isLoading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView(
                    "No devices yet",
                    systemImage: "iphone",
                    description: Text("Pair an agent to register this device.")
                )
            }
        } else {
            List {
                ForEach(model.devices, id: \.device_id) { device in
                    DeviceRow(device: device, isCurrent: model.isCurrent(device)) {
                        model.revokeTarget = device
                    }
                }
            }
            .listStyle(.plain)
        }
    }
}

private struct DeviceRow: View {
    let device: DeviceResponse
    let isCurrent: Bool
    let onRevoke: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(device.name ?? "Unnamed device").font(.headline)
                    if isCurrent {
                        Text("This device")
                            .font(MobaideTypography.labelSmall)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.accentColor.opacity(0.15), in: Capsule())
                    }
                }
                Text(device.device_id)
                    .font(MobaideTypography.code)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Text("Added: \(device.created_at)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text("Last seen: \(device.last_seen_at ?? "—")")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            Button("Revoke", role: .destructive, action: onRevoke)
                .buttonStyle(.bordered)
        }
        .padding(.vertical, 4)
    }
}
