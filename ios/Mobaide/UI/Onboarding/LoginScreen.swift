import AuthenticationServices
import Observation
import SwiftUI

/// One-shot navigation events shared by the Login and Register flows
/// (Android `LoginNavEvent`/`RegisterNavEvent` parity — the cases were
/// identical, so iOS uses one enum).
enum AuthNavEvent: Equatable, Sendable {
    /// Session persisted; the auth-state change flips the root screen — the
    /// view just clears any pushed path.
    case success
    case verificationRequired(email: String)
}

/// Mirror of Android `LoginScreen`: email/password sign-in, Google and Apple
/// provider buttons, and session-expiry toasts. Rendered as the stack root
/// while `unauthenticated`.
struct LoginScreen: View {
    @Environment(\.appContainer) private var container

    var body: some View {
        if let container {
            LoginContent(container: container)
        }
    }
}

private struct LoginContent: View {
    @State private var model: LoginModel
    @State private var toast: ToastMessage?
    @State private var passwordRevealed = false
    @FocusState private var focusedField: Field?

    @Environment(Router.self) private var router
    @Environment(\.mobaideColors) private var colors
    @Environment(\.colorScheme) private var colorScheme

    private enum Field {
        case email, password
    }

    init(container: AppContainer) {
        _model = State(initialValue: LoginModel(
            authRepository: container.authRepository,
            sessionManager: container.sessionManager,
            signInService: container.signInService
        ))
    }

    var body: some View {
        @Bindable var model = model
        ScrollView {
            VStack(spacing: 20) {
                PixelWordmark(width: 152)

                VStack(spacing: 16) {
                    Text("Sign in to your account")
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

                    PixelLabeledField(label: "Password") {
                        SecretTextField(
                            text: $model.password,
                            label: "Password",
                            revealed: $passwordRevealed,
                            isFocused: focusedField == .password
                        )
                            .textContentType(.password)
                            .submitLabel(.done)
                            .focused($focusedField, equals: .password)
                            .onSubmit { Task { await model.loginWithPassword() } }
                    }

                    if let error = model.error {
                        PixelErrorBanner(error)
                    }

                    Button("Forgot password?") {
                        router.navigate(to: .forgotPassword)
                    }
                    .font(.footnote)
                    .disabled(model.isLoading)
                    .frame(maxWidth: .infinity, alignment: .trailing)

                    Button {
                        Task { await model.loginWithPassword() }
                    } label: {
                        HStack(spacing: 10) {
                            Text("Sign In")
                            if model.isLoading {
                                PixelLoadingBlock(width: 7, height: 13)
                            }
                        }
                    }
                    .buttonStyle(.pixelPrimaryBlock)
                    .disabled(model.isLoading)

                    orDivider

                    GoogleSignInButtonView(label: .signIn) {
                        Task { await model.signInWithGoogle() }
                    }
                    .disabled(model.isLoading)

                    SignInWithAppleButton(.signIn) { request in
                        model.prepareAppleRequest(request)
                    } onCompletion: { result in
                        Task { await model.completeApple(result) }
                    }
                    .signInWithAppleButtonStyle(colorScheme == .dark ? .white : .black)
                    .providerButtonFrame()
                    .disabled(model.isLoading)

                    Button("Don't have an account? Create one") {
                        router.navigate(to: .register)
                    }
                    .font(.footnote)
                    .disabled(model.isLoading)
                }
                .pixelPanel()

                HStack(spacing: 22) {
                    Button("Help & Support") {
                        router.navigate(to: .support)
                    }
                    .disabled(model.isLoading)
                    if let privacyURL = URL(string: AppConfig.privacyPolicyURL) {
                        Link("Privacy policy", destination: privacyURL)
                    }
                }
                .chromeText()
                .foregroundStyle(colors.textMuted)
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
        .task {
            await model.observeSessionEvents()
        }
        #if DEBUG
        .task {
            await model.signInWithDevLoginIfRequested()
        }
        #endif
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
final class LoginModel {
    var email = "" {
        didSet { if oldValue != email { error = nil } }
    }
    var password = "" {
        didSet { if oldValue != password { error = nil } }
    }
    private(set) var isLoading = false
    private(set) var error: String?

    private let authRepository: AuthRepository
    private let sessionManager: SessionManager
    private let signInService: SignInService
    private let navBroadcaster = Broadcaster<AuthNavEvent>(replay: .none)
    private let messageBroadcaster = Broadcaster<String>(replay: .none)

    init(
        authRepository: AuthRepository,
        sessionManager: SessionManager,
        signInService: SignInService
    ) {
        self.authRepository = authRepository
        self.sessionManager = sessionManager
        self.signInService = signInService
    }

    func navEvents() -> AsyncStream<AuthNavEvent> { navBroadcaster.subscribe() }

    func messages() -> AsyncStream<String> { messageBroadcaster.subscribe() }

    /// Driven from the view's `.task` so the subscription lives exactly as
    /// long as the screen. `sessionEvents()` replays the last invalidation,
    /// so an expiry that fired before this screen appeared still surfaces.
    func observeSessionEvents() async {
        for await reason in sessionManager.sessionEvents() {
            switch reason {
            case .tokenRejected:
                messageBroadcaster.emit("Your session has expired. Please sign in again.")
            case .accountRemoved:
                messageBroadcaster.emit(
                    "Your account was removed by an administrator. Sign up to start again or contact support."
                )
            case .manual:
                break
            }
        }
    }

    func loginWithPassword() async {
        guard !isLoading else { return }
        let trimmedEmail = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedEmail.isEmpty, !password.isEmpty else {
            emitError("Email and password are required")
            return
        }
        isLoading = true
        do {
            let result = try await authRepository.loginWithPassword(email: trimmedEmail, password: password)
            handleAuthResult(result)
        } catch is AdminLoginNotSupported {
            emitError(AdminLoginNotSupported.userMessage)
        } catch let limited as LoginRateLimited {
            emitError(limited.userMessage)
        } catch let apiError as APIError {
            if apiError.code == 401 {
                emitError("Invalid email or password")
            } else {
                emitError("Login failed: \(apiError.description)")
            }
        } catch {
            emitError("Login failed: \(String(describing: error))")
        }
    }

    #if DEBUG
    static let devLoginEmailKey = "MobaideDevLoginEmail"
    static let devLoginPasswordKey = "MobaideDevLoginPassword"
    private static var devLoginConsumed = false

    /// Debug builds only: `tests/e2e/manual_stack.py` launches the app with
    /// `-MobaideDevLoginEmail <email> -MobaideDevLoginPassword <password>`
    /// (UserDefaults' volatile argument domain) to sign in to its throwaway
    /// stack. Once per process, so a manual sign-out afterwards sticks.
    func signInWithDevLoginIfRequested(defaults: UserDefaults = .standard) async {
        guard !Self.devLoginConsumed,
              let email = defaults.string(forKey: Self.devLoginEmailKey), !email.isEmpty,
              let password = defaults.string(forKey: Self.devLoginPasswordKey), !password.isEmpty
        else { return }
        Self.devLoginConsumed = true
        self.email = email
        self.password = password
        await loginWithPassword()
    }
    #endif

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

    /// Android `emitError` parity: inline error state AND a one-shot toast,
    /// so a repeated identical error still gets surfaced.
    private func emitError(_ message: String) {
        isLoading = false
        error = message
        messageBroadcaster.emit(message)
    }
}
