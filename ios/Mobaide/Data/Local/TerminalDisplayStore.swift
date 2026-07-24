import Foundation
import Observation

/// App-scoped single source of truth for the terminal display setting (Android
/// `TerminalViewModel.displayConfig` combined with `SettingsViewModel` state).
/// Wraps the non-reactive `PreferencesStore` in `@Observable` so the Settings
/// controls and every live terminal surface stay in sync: mutating the setting
/// re-renders the terminal host, which re-lays-out the SwiftTerm view and — via
/// its `sizeChanged` callback — drives the PTY resize, with no extra plumbing.
@MainActor
@Observable
final class TerminalDisplayStore {
    private(set) var mode: TerminalDisplayMode
    private(set) var staticColumns: Int

    var config: TerminalDisplayConfig {
        TerminalDisplayConfig(mode: mode, staticColumns: staticColumns)
    }

    private let preferences: PreferencesStore

    init(preferences: PreferencesStore) {
        self.preferences = preferences
        self.mode = preferences.getTerminalDisplayMode()
        self.staticColumns = preferences.getTerminalStaticColumns()
    }

    func setMode(_ mode: TerminalDisplayMode) {
        guard mode != self.mode else { return }
        self.mode = mode
        preferences.setTerminalDisplayMode(mode)
    }

    func setStaticColumns(_ columns: Int) {
        let clamped = TerminalDisplayDefaults.clampColumns(columns)
        guard clamped != staticColumns else { return }
        staticColumns = clamped
        preferences.setTerminalStaticColumns(clamped)
    }
}
