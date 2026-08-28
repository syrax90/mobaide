import SwiftUI

/// Install commands for a fresh agent host (Android `InstallCommandsSheet`).
/// Freely dismissable; shows both one-liners since the operator installs on a
/// different machine. iOS shares each via `ShareLink` (vs. Android's copy).
struct InstallCommandsSheet: View {
    let onDone: () -> Void

    private static let linuxMacOS = "curl -fsSL https://get.mobaide.com/install.sh | bash"
    private static let windows = "irm https://get.mobaide.com/install.ps1 | iex"

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Run one of these on the machine that should host the agent, then pair it here.")
                        .font(MobaideTypography.bodyLarge)
                        .foregroundStyle(colors.textMuted)

                    InstallCommandCard(title: "Linux / macOS", command: Self.linuxMacOS)
                    InstallCommandCard(title: "Windows (PowerShell)", command: Self.windows)
                }
                .padding()
            }
            .pixelScreenBackground()
            .navigationTitle("Install commands")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { onDone() }
                }
            }
        }
        .pixelSheet()
    }
}

private struct InstallCommandCard: View {
    let title: String
    let command: String

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .sectionHeading()
            HStack(alignment: .center, spacing: 8) {
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(command)
                        .font(MobaideTypography.code)
                        .foregroundStyle(colors.text)
                        .lineLimit(1)
                        .textSelection(.enabled)
                }
                Spacer(minLength: 0)
                ShareLink(item: command) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.system(size: 16))
                        .foregroundStyle(colors.accent)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Share \(title) install command")
            }
        }
        .pixelCodePlate(insets: EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 0))
    }
}
