import SwiftUI

/// The session `⋯` menu's Git section, shared by the terminal and chat screens.
/// Every mutating entry hangs off its `capabilities.git` key; Branches… also
/// needs a project-backed session.
struct SessionGitMenuItems: View {
    let sessionId: String
    let projectName: String?
    let capabilities: GitCapabilities
    let navigate: (Route) -> Void

    var body: some View {
        Section("Git") {
            Button {
                navigate(.sessionGitChanges(sessionId: sessionId))
            } label: {
                Label("Changes…", systemImage: "arrow.triangle.branch")
            }
            if capabilities.commit {
                Button {
                    navigate(.sessionGitChanges(sessionId: sessionId, commit: true))
                } label: {
                    Label("Commit…", systemImage: "checkmark.circle")
                }
            }
            if capabilities.hasSync {
                Button {
                    navigate(.sessionGitChanges(sessionId: sessionId, sync: true))
                } label: {
                    Label("Fetch / Pull / Push…", systemImage: "arrow.triangle.2.circlepath")
                }
            }
            if capabilities.branches, let projectName {
                Button {
                    navigate(.gitManagement(projectName: projectName))
                } label: {
                    Label("Branches…", systemImage: "point.topleft.down.to.point.bottomright.curvepath")
                }
            }
            if capabilities.rebase {
                Button {
                    navigate(.sessionGitChanges(sessionId: sessionId, rebase: true))
                } label: {
                    Label("Rebase…", systemImage: "arrow.triangle.merge")
                }
            }
        }
    }
}
