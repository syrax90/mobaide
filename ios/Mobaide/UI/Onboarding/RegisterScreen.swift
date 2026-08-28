import AuthenticationServices
import Observation
import SwiftUI

/// Mirror of Android `RegisterScreen`: email/password/confirm registration
/// plus the same Google/Apple provider buttons as Login (both delegate to
/// the shared `SignInService`).
struct RegisterScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            RegisterContent(container: container)
        }
    }
}

private struct RegisterContent: View {
    @State private var model: RegisterModel
    @State private var toast: ToastMessage?
    @State private var passwordRevealed = false
    @State private var confirmRevealed = false
    @FocusState private var focusedField: Field?

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors
    @Environment(\.colorScheme) private var colorScheme

    private enum Field {
        case email, password, confirm
    }

    init(container: AppContainer) {
        _model = State(initialValue: RegisterModel(
            authRepository: container.authRepository,
            signInService: container.signInService
        ))
    }

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(spacing: 20) {
                PixelWordmark(width: 152)

                VStack(spacing: 16) {
                    Text("Create your account")
                        .font(MobaideTypography.bodyLarge)
                        .foregroundStyle(colors.textMuted)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    PixelLabeledField(label: "Email") {
                        TextField(text: $model.email, prompt: Text(verbatim: "you@example.com").foregroundStyle(colors.gray)) { Text("Email") }
                            .keyboardType(.emailAddress)
                            .textContentType(.username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(.next)
                            .focused($focusedField, equals: .email)
                            .onSubmit { focusedField = .password }
                            .pixelField(isFocused: focusedField == .email)
                    }

                    // Both fields carry .newPassword — a .password/.newPassword
                    // mix breaks iOS AutoFill's strong-password suggestion.
                    PixelLabeledField(label: "Password") {
                        SecretTextField(text: $model.password, label: "Password", revealed: $passwordRevealed)
                            .textContentType(.newPassword)
                            .submitLabel(.next)
                            .focused($focusedField, equals: .password)
                            .onSubmit { focusedField = .confirm }
                    }

                    PixelLabeledField(label: "Confirm password") {
                        SecretTextField(text: $model.confirmPassword, label: "Confirm password", revealed: $confirmRevealed)
                            .textContentType(.newPassword)
                            .submitLabel(.done)
                            .focused($focusedField, equals: .confirm)
                            .onSubmit { Task { await model.register() } }
                    }

                    if let error = model.error {
                        PixelErrorBanner(error)
                    }

                    Button {
                        Task { await model.register() }
                    } label: {
                        HStack(spacing: 10) {
                            Text("Create Account")
                            if model.isLoading {
                                PixelLoadingBlock(width: 7, height: 13)
                            }
                        }
                    }
                    .buttonStyle(.pixelPrimaryBlock)
                    .disabled(model.isLoading)

                    orDivider

                    GoogleSignInButtonView(label: .signUp) {
                        Task { await model.signInWithGoogle() }
                    }
                    .disabled(model.isLoading)

                    SignInWithAppleButton(.signUp) { request in
                        model.prepareAppleRequest(request)
                    } onCompletion: { result in
                        Task { await model.completeApple(result) }
                    }
                    .signInWithAppleButtonStyle(colorScheme == .dark ? .white : .black)
                    .providerButtonFrame()
                    .disabled(model.isLoading)

                    VStack(spacing: 4) {
                        Text("By creating an account, you agree to our")
                            .font(.footnote)
                            .foregroundStyle(colors.textMuted)
                        if let privacyURL = URL(string: AppConfig.privacyPolicyURL) {
                            Link("Privacy policy", destination: privacyURL)
                                .font(.footnote)
                        }
                    }

                    Button("Already have an account? Sign in") {
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
            for await event in model.navEvents() {
                switch event {
                case .success:
                    router.popToRoot()
                case .verificationRequired(let email):
                    router.navigate(to: .verifyEmail(email: email))
                }
            }
        }
        .task {
            for await text in model.messages() {
                toast = ToastMessage(text: text)
            }
        }
    }

    private var orDivider: some View {
        HStack(spacing: 12) {
            PixelDivider()
            Text("or")
                .chromeText()
                .foregroundStyle(colors.gray)
            PixelDivider()
        }
    }
}

@MainActor
@Observable
final class RegisterModel {
    var email = "" {
        didSet { if oldValue != email { error = nil } }
    }
    var password = "" {
        didSet { if oldValue != password { error = nil } }
    }
    var confirmPassword = "" {
        didSet { if oldValue != confirmPassword { error = nil } }
    }
    private(set) var isLoading = false
    private(set) var error: String?

    private let authRepository: AuthRepository
    private let signInService: SignInService
    private let navBroadcaster = Broadcaster<AuthNavEvent>(replay: .none)
    private let messageBroadcaster = Broadcaster<String>(replay: .none)

    init(authRepository: AuthRepository, signInService: SignInService) {
        self.authRepository = authRepository
        self.signInService = signInService
    }

    func navEvents() -> AsyncStream<AuthNavEvent> { navBroadcaster.subscribe() }

    func messages() -> AsyncStream<String> { messageBroadcaster.subscribe() }

    func register() async {
        guard !isLoading else { return }
        let trimmedEmail = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedEmail.isEmpty, !password.isEmpty else {
            emitError("Email and password are required")
            return
        }
        guard password == confirmPassword else {
            emitError("Passwords do not match")
            return
        }
        isLoading = true
        do {
            let result = try await authRepository.register(email: trimmedEmail, password: password)
            handleAuthResult(result)
        } catch is AdminLoginNotSupported {
            emitError(AdminLoginNotSupported.userMessage)
        } catch let apiError as APIError {
            if apiError.code == 409 {
                emitError("An account with this email already exists")
            } else {
                emitError("Registration failed: \(apiError.description)")
            }
        } catch {
            emitError("Registration failed: \(String(describing: error))")
        }
    }

    func signInWithGoogle() async {
        guard !isLoading else { return }
        isLoading = true
        let outcome = await signInService.signInWithGoogle()
        handleSignInOutcome(outcome)
    }

    func prepareAppleRequest(_ request: ASAuthorizationAppleIDRequest) {
        isLoading = true
        signInService.prepareAppleRequest(request)
    }

    func completeApple(_ result: Result<ASAuthorization, any Error>) async {
        let outcome = await signInService.handleAppleCompletion(result)
        handleSignInOutcome(outcome)
    }

    func handleSignInOutcome(_ outcome: SignInService.Outcome) {
        switch outcome {
        case .success:
            isLoading = false
            navBroadcaster.emit(.success)
        case .verificationRequired(let email):
            isLoading = false
            navBroadcaster.emit(.verificationRequired(email: email))
        case .cancelled:
            isLoading = false
        case .failed(let message):
            emitError(message)
        }
    }

    private func handleAuthResult(_ result: AuthResult) {
        switch result {
        case .success:
            isLoading = false
            navBroadcaster.emit(.success)
        case .verificationRequired(let email):
            isLoading = false
            navBroadcaster.emit(.verificationRequired(email: email))
        }
    }

    private func emitError(_ message: String) {
        isLoading = false
        error = message
        messageBroadcaster.emit(message)
    }
}
