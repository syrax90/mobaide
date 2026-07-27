import Foundation

/// A server-synced saved shortcut projected for rendering (Android
/// `SavedShortcutUi`): the control-row button and picker chip label falls back
/// to the chord's display string when the user never named it.
struct SavedShortcutUi: Identifiable, Equatable, Sendable {
    static let maxNameLength = 100

    let id: String
    let chord: KeyChord
    let name: String?
    let risky: Bool

    var label: String { name ?? chord.display }
}
