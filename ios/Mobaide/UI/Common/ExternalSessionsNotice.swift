import SwiftUI

/// Mirror of Android `ExternalSessionsNotice`. Informational rather than
/// alarming — the web `.notice-banner` plate with a `rule` ring, not a danger
/// tone, because nothing is broken. Dismissal is permanent, which is why the
/// affordance is a labelled button rather than a bare close glyph.
struct ExternalSessionsNotice: View {
    let onDismiss: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Sessions started outside Mobaide won't appear here", systemImage: "info.circle")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(colors.text)
            Text(
                "Mobaide lists only sessions its own agent started. To continue a Claude Code or "
                    + "Codex session from your PC, start a new session of that agent and choose "
                    + "Continue a session."
            )
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
            Button("Got it", action: onDismiss)
                .buttonStyle(.pixelSmall)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelInfoBanner()
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }
}
