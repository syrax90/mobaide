import Observation
import SwiftUI

/// Mirror of Android `ResetPasswordScreen`: consume the emailed 6-digit code
/// and choose a new password. Success pops back to the login root.
struct ResetPasswordScreen: View {
    let email: String

    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            ResetPasswordContent(email: email, container: container)
        }
    }
}

private struct ResetPasswordContent: View {
    @State private var model: ResetPasswordModel
    @State private var toast: ToastMessage?
    @State private var passwordRevealed = false
    @State private var confirmRevealed = false
    @FocusState private var focusedField: Field?

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors

    private enum Field {
        case newPassword, confirm
    }

    init(email: String, container: AppContainer) {
        _model = State(initialValue: ResetPasswordModel(
            email: email,
            authRepository: container.authRepository
        ))
    }

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(spacing: 20) {
                PixelWordmark(width: 152)

                VStack(spacing: 16) {
                    Text("Choose a new password")
                        .pageTitle()
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text("We sent a 6-digit code to \(EmailFormatting.maskEmail(model.email)).")
                        .font(MobaideTypography.bodyLarge)
                        .foregroundStyle(colors.textMuted)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    CodeInputField(
                        code: $model.code,
                        enabled: !model.isLoading,
                        isError: model.error != nil,
                        accessibilityLabelText: "Reset code",
                        onSubmit: { focusedField = .newPassword }
                    )

                    PixelLabeledField(label: "New password") {
                        SecretTextField(
                            text: $model.newPassword,
                            label: "New password",
                            revealed: $passwordRevealed,
                            isFocused: focusedField == .newPassword
                        )
                            .textContentType(.newPassword)
                            .submitLabel(.next)
                            .focused($focusedField, equals: .newPassword)
                            .onSubmit { focusedField = .confirm }
                    }

                    PixelLabeledField(label: "Confirm password") {
                        SecretTextField(
                            text: $model.confirmPassword,
                            label: "Confirm password",
                            revealed: $confirmRevealed,
                            isFocused: focusedField == .confirm
                        )
                            .textContentType(.newPassword)
                            .submitLabel(.done)
                            .focused($focusedField, equals: .confirm)
                            .onSubmit { Task { await model.submit() } }
                    }

                    if let error = model.error {
                        PixelErrorBanner(error)
                    }

                    Button {
                        Task { await model.submit() }
                    } label: {
                        HStack(spacing: 10) {
                            Text("Reset password")
                            if model.isLoading {
                                PixelLoadingBlock(width: 7, height: 13)
                            }
                        }
                    }
                    .buttonStyle(.pixelPrimaryBlock)
                    .disabled(model.isLoading)

                    Button(resendLabel) {
                        Task { await model.resend() }
                    }
                    .font(.footnote)
                    .disabled(model.isResending || model.cooldownSeconds > 0)

                    Button("Back to sign in") {
                        router.popToRoot()
                    }
                    .font(.footnote)
                    .disabled(model.isLoading)
                }
                .pixelPanel()
            }
            .padding(24)
        }
        .pixelScreenBackground()
        .toast($toast)
        .task {
            for await _ in model.successEvents() {
                router.popToRoot()
            }
        }
        .task {
            for await text in model.messages() {
                toast = ToastMessage(text: text)
            }
        }
    }

    private var resendLabel: String {
        if model.isResending { return "Sending…" }
        if model.cooldownSeconds > 0 { return "Resend code (\(model.cooldownSeconds)s)" }
        return "Resend code"
    }
}

@MainActor
@Observable
final class ResetPasswordModel {
    static let codeLength = 6
    static let defaultCooldownSeconds = 60

    let email: String
    var code = "" {
        didSet { if oldValue != code { error = nil } }
    }
    var newPassword = "" {
        didSet { if oldValue != newPassword { error = nil } }
    }
    var confirmPassword = "" {
        didSet { if oldValue != confirmPassword { error = nil } }
    }
    private(set) var isLoading = false
    private(set) var isResending = false
    private(set) var cooldownSeconds = 0
    private(set) var error: String?
    private(set) var cooldownTask: Task<Void, Never>?

    private let authRepository: AuthRepository
    private let sleeper: any Sleeper
    private let successBroadcaster = Broadcaster<Bool>(replay: .none)
    private let messageBroadcaster = Broadcaster<String>(replay: .none)

    init(
        email: String,
        authRepository: AuthRepository,
        sleeper: any Sleeper = ContinuousSleeper()
    ) {
        self.email = email
        self.authRepository = authRepository
        self.sleeper = sleeper
    }

    func successEvents() -> AsyncStream<Bool> { successBroadcaster.subscribe() }

    func messages() -> AsyncStream<String> { messageBroadcaster.subscribe() }

    func submit() async {
        guard !isLoading else { return }
        guard code.count == Self.codeLength else {
            emitError("Enter the 6-digit code we emailed you")
            return
        }
        guard newPassword.count >= PasswordRules.minLength else {
            emitError(PasswordRules.tooShortMessage)
            return
        }
        guard newPassword == confirmPassword else {
            emitError("Passwords don't match")
            return
        }
        isLoading = true
        do {
            try await authRepository.resetPassword(email: email, code: code, newPassword: newPassword)
            isLoading = false
            successBroadcaster.emit(true)
        } catch let failure as ResetPasswordFailure {
            handleFailure(failure.reason)
        } catch {
            emitError("Couldn't reset password: \(String(describing: error))")
        }
    }

    func resend() async {
        guard !isResending, cooldownSeconds == 0 else { return }
        guard !email.isEmpty else { return }
        isResending = true
        do {
            switch try await authRepository.requestPasswordReset(email: email) {
            case .sent:
                startCooldown(Self.defaultCooldownSeconds)
                messageBroadcaster.emit("A new code is on its way.")
            case .rateLimited(let retryAfterSeconds):
                startCooldown(retryAfterSeconds)
                messageBroadcaster.emit(
                    "Please wait \(retryAfterSeconds)s before requesting another code."
                )
            }
        } catch {
            emitError("Couldn't request a new code: \(String(describing: error))")
        }
        isResending = false
    }

    private func handleFailure(_ reason: ResetPasswordError) {
        switch reason {
        case .invalidCode:
            code = ""
            emitError("That code isn't right. Try again.")
        case .expiredCode:
            emitError("That code expired. Tap \"Resend\" to get a new one.")
        case .attemptsExhausted:
            emitError("Too many attempts. Request a new code.")
        case .sameAsOld:
            emitError("New password must differ from your current password.")
        case .ipRateLimited(let retryAfterSeconds):
            emitError("Too many requests from this network. Try again in \(retryAfterSeconds)s.")
        case .unknown(let code, let message):
            emitError("Couldn't reset password: \(message.isEmpty ? "Unknown error (\(code))" : message)")
        }
    }

    private func startCooldown(_ seconds: Int) {
        cooldownTask?.cancel()
        guard seconds > 0 else { return }
        cooldownSeconds = seconds
        cooldownTask = Task { [weak self, sleeper] in
            while !Task.isCancelled {
                guard (try? await sleeper.sleep(for: .seconds(1))) != nil else { return }
                guard let self, !Task.isCancelled else { return }
                self.cooldownSeconds -= 1
                if self.cooldownSeconds <= 0 { return }
            }
        }
    }

    private func emitError(_ message: String) {
        isLoading = false
        isResending = false
        error = message
        messageBroadcaster.emit(message)
    }
}
