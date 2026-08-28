import Foundation
import Observation

/// App-scoped single source of truth for the appearance preference, wrapping
/// the non-reactive `PreferencesStore` in `@Observable` so the Settings picker
/// and the root `preferredColorScheme` stay in sync.
@MainActor
@Observable
final class ThemeStore {
    private(set) var preference: ThemePreference

    private let preferences: PreferencesStore

    init(preferences: PreferencesStore) {
        self.preferences = preferences
        self.preference = preferences.getThemePreference()
    }

    func setPreference(_ preference: ThemePreference) {
        guard preference != self.preference else { return }
        self.preference = preference
        preferences.setThemePreference(preference)
    }
}
