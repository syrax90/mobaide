import SwiftUI

/// A single workspace row (Android `WorkspaceCard`): name, a path row with a red
/// warning glyph when the path is missing, and status chips.
struct WorkspaceCard: View {
    let workspace: WorkspaceInfo

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(workspace.name)
                .font(.body.weight(.semibold))
                .foregroundStyle(colors.text)

            HStack(spacing: 4) {
                if !workspace.path_exists {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.caption2)
                        .foregroundStyle(colors.danger)
                        .accessibilityLabel("Path does not exist")
                }
                Text(workspace.path)
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            if hasChips {
                HStack(spacing: 6) {
                    if workspace.is_git { WorkspaceChip(text: "git") }
                    if workspace.has_linear_key { WorkspaceChip(text: "Linear key", systemImage: "key.fill") }
                    if let env = workspace.conda_env, !env.isEmpty { WorkspaceChip(text: "conda:\(env)") }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
    }

    private var hasChips: Bool {
        workspace.is_git || workspace.has_linear_key || !(workspace.conda_env ?? "").isEmpty
    }
}

/// Web `.workspace-badge`: a muted status chip.
private struct WorkspaceChip: View {
    let text: String
    var systemImage: String?

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 4) {
            if let systemImage {
                Image(systemName: systemImage).font(.caption2)
            }
            Text(text).chromeText()
        }
        .foregroundStyle(colors.textMuted)
        .padding(EdgeInsets(top: 2, leading: 6, bottom: 2, trailing: 6))
        .background(colors.surface)
        .pixelRing(colors.rule)
    }
}
