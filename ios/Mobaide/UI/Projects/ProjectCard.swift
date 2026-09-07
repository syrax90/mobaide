import SwiftUI

/// A single project row (Android `ProjectCard`): name, a path row with a red
/// warning glyph when the path is missing, and status chips.
struct ProjectCard: View {
    let project: ProjectInfo

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(project.name)
                .font(.body.weight(.semibold))
                .foregroundStyle(colors.text)

            HStack(spacing: 4) {
                if !project.path_exists {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.caption2)
                        .foregroundStyle(colors.danger)
                        .accessibilityLabel("Path does not exist")
                }
                Text(project.path)
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }

            if hasChips {
                HStack(spacing: 6) {
                    if project.is_git { ProjectChip(text: "git") }
                    if project.has_linear_key { ProjectChip(text: "Linear key", systemImage: "key.fill") }
                    if let env = project.conda_env, !env.isEmpty { ProjectChip(text: "conda:\(env)") }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
    }

    private var hasChips: Bool {
        project.is_git || project.has_linear_key || !(project.conda_env ?? "").isEmpty
    }
}

/// Web `.project-badge`: a muted status chip.
private struct ProjectChip: View {
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
