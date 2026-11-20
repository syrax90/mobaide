import SwiftUI

/// "Open sessions in": the chat view or the agent's own terminal app. Shared
/// by a normal agent session and Auto Scan. A non-nil `chatUnavailableReason`
/// disables Chat, shows the reason as its subtitle and shows Terminal selected.
struct SessionViewChooserSection<Extra: View>: View {
    let chatSelected: Bool
    let chatUnavailableReason: String?
    let onChoose: (_ chat: Bool) -> Void
    @ViewBuilder let extra: () -> Extra

    init(
        chatSelected: Bool,
        chatUnavailableReason: String?,
        onChoose: @escaping (_ chat: Bool) -> Void,
        @ViewBuilder extra: @escaping () -> Extra
    ) {
        self.chatSelected = chatSelected
        self.chatUnavailableReason = chatUnavailableReason
        self.onChoose = onChoose
        self.extra = extra
    }

    var body: some View {
        let chatAvailable = chatUnavailableReason == nil
        let chat = chatAvailable && chatSelected
        Section {
            SelectableRow(
                title: "Chat",
                subtitle: chatUnavailableReason ?? "Messages, tool steps and approvals in the app.",
                selected: chat
            ) {
                onChoose(true)
            }
            .disabled(!chatAvailable)
            .accessibilityIdentifier("session_view_chat")
            SelectableRow(
                title: "Terminal",
                subtitle: "The agent's own terminal app.",
                selected: !chat
            ) {
                onChoose(false)
            }
            .accessibilityIdentifier("session_view_terminal")
            extra()
        } header: {
            PixelSectionHeader("Open sessions in")
        }
    }
}

extension SessionViewChooserSection where Extra == EmptyView {
    init(
        chatSelected: Bool,
        chatUnavailableReason: String?,
        onChoose: @escaping (_ chat: Bool) -> Void
    ) {
        self.init(
            chatSelected: chatSelected,
            chatUnavailableReason: chatUnavailableReason,
            onChoose: onChoose,
            extra: { EmptyView() }
        )
    }
}
