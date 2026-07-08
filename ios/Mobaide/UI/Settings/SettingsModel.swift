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

    init(authRepository: AuthRepository) {
        self.authRepository = authRepository
        self.email = authRepository.getUserEmail() ?? "—"
    }

    func openPrivacyPolicy() {
        safariURL = URL(string: AppConfig.privacyPolicyURL)
    }

    func logout() async {
        await authRepository.logout()
    }
}
