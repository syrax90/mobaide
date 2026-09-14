import SwiftUI

/// A whole-screen state of the Changes / Diff screens that still pulls to
/// refresh: the plate sits in a `ScrollView` so `.refreshable` keeps working
/// (the `GitScreen` empty-state recipe).
struct SessionGitStateView<Actions: View>: View {
    let title: String
    let systemImage: String
    let description: String
    var detail: String? = nil
    @ViewBuilder let actions: () -> Actions

    @Environment(\.mobaideColors) private var colors

    init(
        _ title: String,
        systemImage: String,
        description: String,
        detail: String? = nil,
        @ViewBuilder actions: @escaping () -> Actions
    ) {
        self.title = title
        self.systemImage = systemImage
        self.description = description
        self.detail = detail
        self.actions = actions
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                PixelEmptyState(title, systemImage: systemImage, description: description, actions: actions)
                if let detail {
                    Text(detail)
                        .font(.caption)
                        .monospaced()
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(2)
                        .truncationMode(.middle)
                        .frame(maxWidth: .infinity)
                }
            }
            .padding(16)
        }
        .pixelScreenBackground()
    }
}

extension SessionGitStateView where Actions == EmptyView {
    init(_ title: String, systemImage: String, description: String, detail: String? = nil) {
        self.init(title, systemImage: systemImage, description: description, detail: detail) { EmptyView() }
    }
}
