import Observation
import SwiftUI

/// Mirror of Android `VerifyEmailScreen`: 6-digit OTP entry with resend
/// cooldown. Success pops the whole onboarding stack (the auth-state change
/// flips the root to the agent list).
struct VerifyEmailScreen: View {
    let email: String

    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            VerifyEmailContent(email: email, container: container)
        }
    }
}

private struct VerifyEmailContent: View {
    @State private var model: VerifyEmailModel
    @State private var toast: ToastMessage?

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors

    init(email: String, container: AppContainer) {
        _model = State(initialValue: VerifyEmailModel(
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
                    Text("Check your email")
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
                        onSubmit: { Task { await model.submit() } }
                    )

                    if let error = model.error {
                        PixelErrorBanner(error)
                    }

                    Button {
                        Task { await model.submit() }
                    } label: {
                        HStack(spacing: 10) {
                            Text("Verify")
                            if model.isLoading {
                                PixelLoadingBlock(width: 7, height: 13)
                            }
                        }
                    }
                    .buttonStyle(.pixelPrimaryBlock)
                    .disabled(model.isLoading || model.code.count != VerifyEmailModel.codeLength)

                    Button(resendLabel) {
                        Task { await model.resend() }
                    }
                    .font(.footnote)
                    .disabled(model.isResending || model.cooldownSeconds > 0)

                    Button("Use a different email") {
                        router.pop()
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
final class VerifyEmailModel {
    static let codeLength = 6
    static let defaultCooldownSeconds = 60

    let email: String
    var code = "" {
        didSet { if oldValue != code { error = nil } }
    }
    private(set) var isLoading = false
    private(set) var isResending = false
    private(set) var cooldownSeconds = 0
    private(set) var error: String?
    /// Exposed so tests can await the countdown deterministically.
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
        isLoading = true
        do {
            _ = try await authRepository.verifyEmail(email: email, code: code)
            isLoading = false
            successBroadcaster.emit(true)
        } catch is AdminLoginNotSupported {
            emitError(AdminLoginNotSupported.userMessage)
        } catch let apiError as APIError {
            code = ""
            emitError(friendlyMessage(apiError))
        } catch {
            emitError("Verification failed: \(String(describing: error))")
        }
    }

    func resend() async {
        guard !isResending, cooldownSeconds == 0 else { return }
        guard !email.isEmpty else { return }
        isResending = true
        do {
            switch try await authRepository.resendVerification(email: email) {
            case .sent:
                startCooldown(Self.defaultCooldownSeconds)
                messageBroadcaster.emit("A new code is on its way.")
            case .rateLimited(let cooldownSecondsRemaining):
                startCooldown(cooldownSecondsRemaining)
                messageBroadcaster.emit(
                    "Please wait \(cooldownSecondsRemaining)s before requesting another code."
                )
            }
        } catch {
            emitError("Couldn't request a new code: \(String(describing: error))")
        }
        isResending = false
    }

    private func friendlyMessage(_ error: APIError) -> String {
        switch error.code {
        case 400: return "That code isn't right. Try again."
        case 410: return "That code expired. Tap \"Resend\" to get a new one."
        case 429: return "Too many attempts. Request a new code."
        default: return "Verification failed: \(error.description)"
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
