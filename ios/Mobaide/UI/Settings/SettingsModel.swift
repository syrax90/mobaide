import Foundation
import Observation

/// Backing model for the Settings screen (Android `SettingsViewModel`, minus
/// the delete-account machine which lives in `DeleteAccountModel`).
@MainActor
@Observable
final class SettingsModel {
    let email: String
    var safariURL: URL?

    private let authRepository: AuthRepository
    private let onSignOut: @MainActor () -> Void

    init(
        authRepository: AuthRepository,
        onSignOut: @escaping @MainActor () -> Void = {}
    ) {
        self.authRepository = authRepository
        self.onSignOut = onSignOut
        self.email = authRepository.getUserEmail() ?? "—"
    }

    func openPrivacyPolicy() {
        safariURL = URL(string: AppConfig.privacyPolicyURL)
    }

    func logout() async {
        // Best-effort server-side push-token clear; must run before the
        // logout wipes the credentials it snapshots.
        onSignOut()
        await authRepository.logout()
    }
}
