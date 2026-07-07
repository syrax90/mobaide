import SwiftUI

/// Shared body for the route placeholders. The typed screen structs below
/// carry their real init signatures so iOS-03…iOS-08 replace only bodies,
/// never the navigation wiring.
struct PlaceholderScreen: View {
    let title: String
    var params: [String: String] = [:]
    var links: [(label: String, route: Route)] = []

    var body: some View {
        List {
            Section {
                ForEach(params.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                    LabeledContent(key, value: value)
                }
            } header: {
                if !params.isEmpty {
                    Text("Parameters")
                }
            }
            if !links.isEmpty {
                Section("Navigate") {
                    ForEach(links, id: \.label) { link in
                        NavigationLink(link.label, value: link.route)
                    }
                }
            }
        }
        .navigationTitle(title)
    }
}

struct AgentListScreen: View {
    var body: some View {
        PlaceholderScreen(title: "Agents", links: [
            ("Sessions", .sessionList),
            ("Session detail", .sessionDetail(sessionId: "demo-session")),
            ("Create session", .createSession),
            ("Projects", .projects),
            ("Git management", .gitManagement(projectName: "demo-project")),
            ("Auto-scan detail", .autoScanDetail(projectName: "demo-project")),
            ("Connections", .connections),
            ("Templates", .templates),
            ("Devices", .devices),
            ("Settings", .settings),
            ("Support", .support),
        ])
    }
}

struct SettingsScreen: View {
    var body: some View { PlaceholderScreen(title: "Settings") }
}

struct SupportScreen: View {
    var body: some View { PlaceholderScreen(title: "Support") }
}

struct AutoScanDetailScreen: View {
    let projectName: String
    var body: some View { PlaceholderScreen(title: "Auto Scan", params: ["projectName": projectName]) }
}

struct TemplatesScreen: View {
    var body: some View { PlaceholderScreen(title: "Templates") }
}

struct DevicesScreen: View {
    var body: some View { PlaceholderScreen(title: "Devices") }
}

/// Full-screen upgrade takeover (rendered outside the NavigationStack).
struct IncompatibleServerScreen: View {
    let info: AuthState.IncompatibleServer

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 48))
                .foregroundStyle(MobaideColors.warningYellow)
            Text("Update required")
                .font(MobaideTypography.titleLarge)
            Text(info.reason)
                .font(MobaideTypography.bodyLarge)
                .multilineTextAlignment(.center)
            if let url = URL(string: info.storeUrl), !info.storeUrl.isEmpty {
                Link("Open the App Store", destination: url)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
    }
}
