import SwiftUI

/// Help & Support (Android `SupportScreen`): the community forum opens in a
/// Safari sheet; "Email support" runs the MFMailCompose → `mailto:` → copy
/// fallback chain with prefilled diagnostics.
struct SupportScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            SupportContent(container: container)
        }
    }
}

private struct SupportContent: View {
    @State private var model: SupportModel
    @State private var toast: ToastMessage?

    @Environment(\.openURL) private var openURL

    init(container: AppContainer) {
        _model = State(initialValue: SupportModel(
            mailComposer: container.mailComposer,
            deviceIdentityStore: container.deviceIdentityStore,
            userEmail: container.authRepository.getUserEmail()
        ))
    }

    var body: some View {
        List {
            Section {
                Button { model.openCommunity() } label: {
                    Label("Ask the community", systemImage: "bubble.left.and.bubble.right")
                }
                Button { model.emailSupport() } label: {
                    Label("Email support", systemImage: "envelope")
                }
            } footer: {
                PixelSectionFooter("Community threads are public and need a free GitHub account. Email support is private and best for account-specific issues.")
            }
            .pixelFormSection()
        }
        .pixelForm()
        .navigationTitle("Help & Support")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: safariPresented) {
            if let url = model.safariURL { SafariView(url: url) }
        }
        .sheet(isPresented: composePresented) {
            if case let .compose(recipient, subject, body) = model.mailAction {
                MailComposeView(recipient: recipient, subject: subject, body: body) {
                    model.clearMailAction()
                }
            }
        }
        .alert("No email app found", isPresented: copyAddressPresented, presenting: copyAddressValue) { _ in
            Button("Copy address") {
                model.copyAddress(model.supportEmail)
                model.clearMailAction()
            }
            Button("Cancel", role: .cancel) { model.clearMailAction() }
        } message: { address in
            Text("Email us at \(address).")
        }
        .onChange(of: model.mailAction) { _, action in
            if case let .openURL(url) = action {
                openURL(url)
                model.clearMailAction()
            }
        }
        .toast($toast)
        .onChange(of: model.message) { _, newValue in
            if let newValue {
                toast = ToastMessage(text: newValue)
                model.clearMessage()
            }
        }
    }

    private var safariPresented: Binding<Bool> {
        Binding(get: { model.safariURL != nil }, set: { if !$0 { model.safariURL = nil } })
    }

    private var composePresented: Binding<Bool> {
        Binding(
            get: { if case .compose = model.mailAction { return true }; return false },
            set: { if !$0 { model.clearMailAction() } }
        )
    }

    private var copyAddressPresented: Binding<Bool> {
        Binding(
            get: { if case .copyAddress = model.mailAction { return true }; return false },
            set: { if !$0 { model.clearMailAction() } }
        )
    }

    private var copyAddressValue: String? {
        if case let .copyAddress(address) = model.mailAction { return address }
        return nil
    }
}
