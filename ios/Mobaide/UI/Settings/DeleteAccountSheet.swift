import AuthenticationServices
import SwiftUI

/// Multi-step delete-account sheet (Android `DeleteAccountDialog`): Explain +
/// mode picker → re-auth (password / Google / Apple) → typed-email confirmation
/// → destructive submit. Driven entirely by `DeleteAccountModel.step`.
struct DeleteAccountSheet: View {
    @Bindable var model: DeleteAccountModel

    @Environment(\.mobaideColors) private var colors
    @Environment(\.colorScheme) private var colorScheme
    @State private var passwordRevealed = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    switch model.step {
                    case .explain(let mode):
                        explain(mode)
                    case .reAuthPassword(_, _, let error, let submitting):
                        reAuthPassword(error: error, submitting: submitting)
                    case .reAuthGoogle(_, let error, let submitting):
                        reAuthGoogle(error: error, submitting: submitting)
                    case .reAuthApple(_, let error, let submitting):
                        reAuthApple(error: error, submitting: submitting)
                    case .confirmEmail(let mode, _, _, let error, let submitting):
                        confirmEmail(mode: mode, error: error, submitting: submitting)
                    case .hidden:
                        EmptyView()
                    }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .pixelScreenBackground()
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { model.dismiss() }
                        .disabled(isSubmitting)
                }
            }
        }
        .pixelSheet()
        .interactiveDismissDisabled(isSubmitting)
    }

    // MARK: - Steps

    @ViewBuilder
    private func explain(_ mode: DeletionMode) -> some View {
        Text("This permanently removes your Mobaide account, your paired agents, templates, and devices. This can't be undone.")
            .font(.callout)
            .foregroundStyle(colors.textMuted)

        PixelLabeledField(label: "Deletion timing") {
            PixelSegmented(
                [(DeletionMode.scheduled, "Schedule (14 days)"), (DeletionMode.immediate, "Immediate")],
                selection: modeBinding
            )
        }

        Text(mode == .scheduled
            ? "Your account is disabled now and permanently deleted after 14 days. Sign in any time before then to cancel."
            : "Your account and all data are deleted right away. There is no grace period.")
            .font(.footnote)
            .foregroundStyle(colors.textMuted)

        Text("Re-authenticate to continue")
            .sectionHeading()
            .padding(.top, 8)
        Button { model.chooseMethod(.password) } label: {
            Label("Use my password", systemImage: "key")
        }
        .buttonStyle(.pixelBlock)
        Button { model.chooseMethod(.google) } label: {
            Label("Continue with Google", systemImage: "g.circle")
        }
        .buttonStyle(.pixelBlock)
        Button { model.chooseMethod(.apple) } label: {
            Label("Continue with Apple", systemImage: "apple.logo")
        }
        .buttonStyle(.pixelBlock)
    }

    @ViewBuilder
    private func reAuthPassword(error: String?, submitting: Bool) -> some View {
        Text("Enter your current password to confirm it's you.")
            .font(.callout)
            .foregroundStyle(colors.textMuted)
        SecretTextField(text: passwordBinding, label: "Password", revealed: $passwordRevealed, isInvalid: error != nil)
            .textContentType(.password)
        errorLabel(error)
        Button { model.confirmPasswordReauth() } label: {
            HStack(spacing: 10) {
                Text("Continue")
                if submitting {
                    PixelLoadingBlock(width: 7, height: 13)
                }
            }
        }
        .buttonStyle(.pixelPrimaryBlock)
        .disabled(submitting)
    }

    @ViewBuilder
    private func reAuthGoogle(error: String?, submitting: Bool) -> some View {
        Text("Sign in with Google again to confirm it's you.")
            .font(.callout)
            .foregroundStyle(colors.textMuted)
        if submitting {
            PixelLoadingBlock().frame(maxWidth: .infinity).padding(.vertical, 8)
        } else {
            GoogleSignInButtonView(label: .continue) {
                Task { await model.startGoogleReauth() }
            }
        }
        errorLabel(error)
    }

    @ViewBuilder
    private func reAuthApple(error: String?, submitting: Bool) -> some View {
        Text("Sign in with Apple again to confirm it's you.")
            .font(.callout)
            .foregroundStyle(colors.textMuted)
        if submitting {
            PixelLoadingBlock().frame(maxWidth: .infinity).padding(.vertical, 8)
        } else {
            SignInWithAppleButton(.continue) { request in
                model.prepareAppleRequest(request)
            } onCompletion: { result in
                model.onAppleCompletion(result)
            }
            .signInWithAppleButtonStyle(colorScheme == .dark ? .white : .black)
            .providerButtonFrame()
        }
        errorLabel(error)
    }

    @ViewBuilder
    private func confirmEmail(mode: DeletionMode, error: String?, submitting: Bool) -> some View {
        Text("Type your email address exactly to confirm:")
            .font(.callout)
            .foregroundStyle(colors.textMuted)
        Text(model.email)
            .font(MobaideTypography.code)
            .foregroundStyle(colors.text)
            .pixelCodePlate()
        TextField(text: typedEmailBinding, prompt: Text(verbatim: "you@example.com").foregroundStyle(colors.gray)) { Text("Email") }
            .keyboardType(.emailAddress)
            .textContentType(.username)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .pixelField(isInvalid: error != nil)
        errorLabel(error)
        Button(role: .destructive) {
            Task { await model.submit() }
        } label: {
            HStack(spacing: 10) {
                Text(mode == .immediate ? "Delete immediately" : "Schedule deletion")
                if submitting {
                    PixelLoadingBlock(width: 7, height: 13)
                }
            }
        }
        .buttonStyle(.pixelDangerBlock)
        .disabled(submitting || !typedEmailMatches)
    }

    // MARK: - Helpers

    @ViewBuilder
    private func errorLabel(_ message: String?) -> some View {
        if let message {
            PixelErrorBanner(message)
        }
    }

    private var title: String {
        switch model.step {
        case .explain, .hidden: return "Delete account"
        case .reAuthPassword, .reAuthGoogle, .reAuthApple: return "Confirm it's you"
        case .confirmEmail: return "Final confirmation"
        }
    }

    private var isSubmitting: Bool {
        switch model.step {
        case .reAuthPassword(_, _, _, let submitting),
             .confirmEmail(_, _, _, _, let submitting):
            return submitting
        case .reAuthGoogle(_, _, let submitting),
             .reAuthApple(_, _, let submitting):
            return submitting
        case .explain, .hidden:
            return false
        }
    }

    private var typedEmailMatches: Bool {
        if case .confirmEmail(_, let typed, _, _, _) = model.step { return typed == model.email }
        return false
    }

    private var modeBinding: Binding<DeletionMode> {
        Binding(get: { model.mode }, set: { model.setMode($0) })
    }

    private var passwordBinding: Binding<String> {
        Binding(
            get: { if case .reAuthPassword(_, let password, _, _) = model.step { return password }; return "" },
            set: { model.onPasswordChange($0) }
        )
    }

    private var typedEmailBinding: Binding<String> {
        Binding(
            get: { if case .confirmEmail(_, let typed, _, _, _) = model.step { return typed }; return "" },
            set: { model.onTypedEmailChange($0) }
        )
    }
}
