import SwiftUI

/// One-time agent credentials (Android `AgentCredentialsSheet`). The token is
/// shown once and can only be dismissed via Done. Token and `.env` copies use
/// the sensitive pasteboard path (local-only, auto-expiring).
struct AgentCredentialsSheet: View {
    let credentials: AgentCredentials
    let onDone: () -> Void

    @State private var os: AgentHostOS = .linux

    @Environment(\.mobaideColors) private var colors

    private var envSnippet: String {
        "RELAY_AGENT_ID=\(credentials.agentId)\nRELAY_AGENT_TOKEN=\(credentials.token)"
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Save these credentials on your agent host — the token is shown only once.")
                        .font(MobaideTypography.bodyLarge)
                        .foregroundStyle(colors.textMuted)

                    CopyableCodeBlock(label: "Agent ID", code: credentials.agentId)
                    CopyableCodeBlock(label: "Token (shown once)", code: credentials.token, isSensitive: true)
                    CopyableCodeBlock(label: ".env", code: envSnippet, isSensitive: true, singleLine: false)

                    PixelLabeledField(label: "Host OS") {
                        PixelSegmented(AgentHostOS.allCases.map { ($0, $0.label) }, selection: $os)
                    }

                    CopyableCodeBlock(label: ".env location", code: os.envPath)
                    CopyableCodeBlock(label: "Restart the agent", code: os.restartCommand, singleLine: false)

                    Text("Tip: prefer QR pairing (Scan) — it delivers credentials to the agent without exposing a token.")
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
                .padding()
            }
            .pixelScreenBackground()
            .navigationTitle(credentials.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { onDone() }
                }
            }
            .interactiveDismissDisabled()
        }
        .pixelSheet()
    }
}

/// Default install-path `.env` location and restart command per host OS
/// (Android `AgentHostOs`).
enum AgentHostOS: String, CaseIterable, Identifiable {
    case linux, macos, windows

    var id: String { rawValue }

    var label: String {
        switch self {
        case .linux: return "Linux"
        case .macos: return "macOS"
        case .windows: return "Windows"
        }
    }

    var envPath: String {
        switch self {
        case .linux: return "/opt/mobaide/mobaide_agent/.env"
        case .macos: return "/usr/local/mobaide/mobaide_agent/.env"
        case .windows: return "C:\\Program Files\\MobaideAgent\\mobaide_agent\\.env"
        }
    }

    var restartCommand: String {
        switch self {
        case .linux:
            return "sudo systemctl restart mobaide-agent"
        case .macos:
            return "launchctl unload ~/Library/LaunchAgents/com.mobaide.agent.plist && "
                + "launchctl load ~/Library/LaunchAgents/com.mobaide.agent.plist"
        case .windows:
            return "Stop-ScheduledTask MobaideAgent; Start-ScheduledTask MobaideAgent"
        }
    }
}
