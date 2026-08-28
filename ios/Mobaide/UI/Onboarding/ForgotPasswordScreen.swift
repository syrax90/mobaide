import Observation
import SwiftUI

/// Mirror of Android `ForgotPasswordScreen`: request a reset code. The
/// confirmation message is enumeration-resistant — identical whether or not
/// the account exists.
struct ForgotPasswordScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            ForgotPasswordContent(container: container)
        }
    }
}

private struct ForgotPasswordContent: View {
    @State private var model: ForgotPasswordModel
    @State private var toast: ToastMessage?

    @FocusState private var emailFocused: Bool

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors

    init(container: AppContainer) {
        _model = State(initialValue: ForgotPasswordModel(
            authRepository: container.authRepository
        ))
    }

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(spacing: 20) {
                PixelWordmark(width: 152)

                VStack(spacing: 16) {
                    Text("Reset your password")
                        .pageTitle()
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text("Enter your email address and we'll send you a 6-digit code.")
                        .font(MobaideTypography.bodyLarge)
                        .foregroundStyle(colors.textMuted)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    PixelLabeledField(label: "Email") {
                        TextField(text: $model.email, prompt: Text(verbatim: "you@example.com").foregroundStyle(colors.gray)) { Text("Email") }
                            .keyboardType(.emailAddress)
                            .textContentType(.username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(.done)
                            .focused($emailFocused)
                            .onSubmit { Task { await model.submit() } }
                            .pixelField(isFocused: emailFocused)
                    }

                    if let error = model.error {
                        PixelErrorBanner(error)
                    }

                    Button {
                        Task { await model.submit() }
                    } label: {
                        HStack(spacing: 10) {
                            Text(sendLabel)
                            if model.isSending {
                                PixelLoadingBlock(width: 7, height: 13)
                            }
                        }
                    }
                    .buttonStyle(.pixelPrimaryBlock)
                    .disabled(model.isSending || model.cooldownSeconds > 0)

                    Button("Back to sign in") {
                        router.pop()
                    }
                    .font(.footnote)
                    .disabled(model.isSending)
                }
                .pixelPanel()
            }
            .padding(24)
        }
        .pixelScreenBackground()
        .toast($toast)
        .task {
            for await email in model.codeSentEvents() {
                router.navigate(to: .resetPassword(email: email))
            }
        }
        .task {
            for await text in model.messages() {
                toast = ToastMessage(text: text)
            }
        }
    }

    private var sendLabel: String {
        model.cooldownSeconds > 0
            ? "Send reset code (\(model.cooldownSeconds)s)"
            : "Send reset code"
    }
}

@MainActor
@Observable
final class ForgotPasswordModel {
    /// Enumeration-resistant confirmation — always shown on a 200, whether
    /// or not the account exists.
    static let genericSentMessage = "If an account exists for that email, we sent a code."

    var email = "" {
        didSet { if oldValue != email { error = nil } }
    }
    private(set) var isSending = false
    private(set) var cooldownSeconds = 0
    private(set) var error: String?
    private(set) var cooldownTask: Task<Void, Never>?

    private let authRepository: AuthRepository
    private let sleeper: any Sleeper
    private let codeSentBroadcaster = Broadcaster<String>(replay: .none)
    private let messageBroadcaster = Broadcaster<String>(replay: .none)

    init(authRepository: AuthRepository, sleeper: any Sleeper = ContinuousSleeper()) {
        self.authRepository = authRepository
        self.sleeper = sleeper
    }

    /// Emits the trimmed email the code was requested for.
    func codeSentEvents() -> AsyncStream<String> { codeSentBroadcaster.subscribe() }

    func messages() -> AsyncStream<String> { messageBroadcaster.subscribe() }

    func submit() async {
        guard !isSending, cooldownSeconds == 0 else { return }
        let trimmed = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.contains("@") else {
            emitError("Enter a valid email address")
            return
        }
        isSending = true
        do {
            switch try await authRepository.requestPasswordReset(email: trimmed) {
            case .sent:
                messageBroadcaster.emit(Self.genericSentMessage)
                codeSentBroadcaster.emit(trimmed)
            case .rateLimited(let retryAfterSeconds):
                startCooldown(retryAfterSeconds)
                messageBroadcaster.emit(
                    "Too many requests from this network. Try again in \(retryAfterSeconds)s."
                )
            }
        } catch {
            emitError("Couldn't request a reset code: \(describe(error))")
        }
        isSending = false
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

    private func describe(_ error: any Error) -> String {
        (error as? APIError)?.description ?? String(describing: error)
    }

    private func emitError(_ message: String) {
        isSending = false
        error = message
        messageBroadcaster.emit(message)
    }
}
