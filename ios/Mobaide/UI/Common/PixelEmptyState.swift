import SwiftUI

/// Web `.empty-state`: a plate that occupies the slot the content would,
/// never floating text.
struct PixelEmptyState<Actions: View>: View {
    let title: String
    var systemImage: String? = nil
    var description: String? = nil
    @ViewBuilder let actions: () -> Actions

    @Environment(\.mobaideColors) private var colors

    init(
        _ title: String,
        systemImage: String? = nil,
        description: String? = nil,
        @ViewBuilder actions: @escaping () -> Actions
    ) {
        self.title = title
        self.systemImage = systemImage
        self.description = description
        self.actions = actions
    }

    var body: some View {
        VStack(spacing: 12) {
            if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 36))
                    .foregroundStyle(colors.gray)
            }
            Text(title)
                .pageTitle()
                .multilineTextAlignment(.center)
            if let description {
                Text(description)
                    .font(.footnote)
                    .foregroundStyle(colors.gray)
                    .multilineTextAlignment(.center)
            }
            actions()
        }
        .frame(maxWidth: .infinity)
        .padding(PixelMetrics.emptyStateInsets)
        .background(colors.bg2)
        .pixelRing(colors.surface2)
    }
}

extension PixelEmptyState where Actions == EmptyView {
    init(_ title: String, systemImage: String? = nil, description: String? = nil) {
        self.init(title, systemImage: systemImage, description: description) { EmptyView() }
    }
}
