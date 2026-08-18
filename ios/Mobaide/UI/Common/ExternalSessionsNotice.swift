import SwiftUI

/// Mirror of Android `ExternalSessionsNotice`. Informational rather than
/// alarming — `surfaceVariant`, not an error tone, because nothing is broken.
/// Dismissal is permanent, which is why the affordance is a labelled button
/// rather than a bare close glyph.
struct ExternalSessionsNotice: View {
    let onDismiss: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Sessions started outside Mobaide won't appear here", systemImage: "info.circle")
                .font(.subheadline.weight(.semibold))
            Text(
                "Mobaide can only open sessions its own agent started. To control a session "
                    + "from here, start it from Mobaide instead of launching it yourself on the "
                    + "agent machine."
            )
            .font(.footnote)
            Button("Got it", action: onDismiss)
                .buttonStyle(.bordered)
        }
        .foregroundStyle(colors.onSurfaceVariant)
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(colors.surfaceVariant, in: RoundedRectangle(cornerRadius: 8))
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }
}
