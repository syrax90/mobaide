import SwiftUI

/// A single project row (Android `ProjectCard`): name, a path row with a red
/// warning glyph when the path is missing, and status chips.
struct ProjectCard: View {
    let project: ProjectInfo

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(project.name)
                .font(.body.weight(.medium))

            HStack(spacing: 4) {
                if !project.path_exists {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.caption2)
                        .foregroundStyle(colors.error)
                        .accessibilityLabel("Path does not exist")
                }
                Text(project.path)
                    .font(.caption)
                    .foregroundStyle(.secondary)
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
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }

    private var hasChips: Bool {
        project.is_git || project.has_linear_key || !(project.conda_env ?? "").isEmpty
    }
}

private struct ProjectChip: View {
    let text: String
    var systemImage: String?

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 4) {
            if let systemImage {
                Image(systemName: systemImage).font(.caption2)
            }
            Text(text).font(.caption2)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .foregroundStyle(colors.onSurfaceVariant)
        .background(colors.surfaceVariant, in: Capsule())
    }
}
