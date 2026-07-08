import SwiftUI

/// Install commands for a fresh agent host (Android `InstallCommandsSheet`).
/// Freely dismissable; shows both one-liners since the operator installs on a
/// different machine. iOS shares each via `ShareLink` (vs. Android's copy).
struct InstallCommandsSheet: View {
    let onDone: () -> Void

    private static let linuxMacOS = "curl -fsSL https://get.mobaide.com/install.sh | bash"
    private static let windows = "irm https://get.mobaide.com/install.ps1 | iex"

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Run one of these on the machine that should host the agent, then pair it here.")
                        .font(MobaideTypography.bodyLarge)
                        .foregroundStyle(.secondary)

                    InstallCommandCard(title: "Linux / macOS", command: Self.linuxMacOS)
                    InstallCommandCard(title: "Windows (PowerShell)", command: Self.windows)
                }
                .padding()
            }
            .navigationTitle("Install commands")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { onDone() }
                }
            }
        }
    }
}

private struct InstallCommandCard: View {
    let title: String
    let command: String

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(MobaideTypography.labelSmall)
                .foregroundStyle(colors.onSurfaceVariant)
            HStack(alignment: .center, spacing: 8) {
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(command)
                        .font(MobaideTypography.code)
                        .lineLimit(1)
                        .textSelection(.enabled)
                }
                Spacer(minLength: 0)
                ShareLink(item: command) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.system(size: 16))
                }
                .accessibilityLabel("Share \(title) install command")
            }
        }
        .padding(EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 12))
        .background(colors.surfaceVariant, in: RoundedRectangle(cornerRadius: 12))
    }
}
