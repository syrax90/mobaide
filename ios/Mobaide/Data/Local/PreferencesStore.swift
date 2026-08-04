import Foundation

/// Mirror of Android `PreferencesStore` (plain DataStore file
/// "mobaide_prefs"): non-secret app preferences, including the
/// `last_used_*` keys and per-project/per-agent dynamic caches.
struct PreferencesStore: @unchecked Sendable {
    static let suiteName = "mobaide_prefs"

    static let keyLastUsedProjectName = "last_used_project_name"
    static let keyLastUsedSessionId = "last_used_session_id"
    static let keyLastUsedClaudeOptions = "last_used_claude_options"
    static let keyLastUsedAutoScanOptions = "last_used_auto_scan_options"
    static let keyLastUsedCodexOptions = "last_used_codex_options"
    static let keyLastUsedAntigravityOptions = "last_used_antigravity_options"
    static let keyTerminalDisplayMode = "terminal_display_mode"
    static let keyTerminalStaticColumns = "terminal_static_columns"

    static let maxRecentCustomPaths = 5
    static let maxRecentShortcutChords = 10

    static func linearLabelsKey(projectName: String) -> String { "linear_labels::\(projectName)" }
    static func linearStatesKey(projectName: String) -> String { "linear_states::\(projectName)" }
    static func recentCustomPathsKey(agentKey: String) -> String { "recent_custom_paths::\(agentKey)" }
    static func recentShortcutChordsKey(kindKey: String) -> String { "recent_shortcut_chords::\(kindKey)" }

    // UserDefaults is documented thread-safe; this struct holds no other
    // mutable state, hence @unchecked Sendable.
    private let defaults: UserDefaults

    init(defaults: UserDefaults = UserDefaults(suiteName: PreferencesStore.suiteName) ?? .standard) {
        self.defaults = defaults
    }

    func getLastUsedProjectName() -> String? {
        defaults.string(forKey: Self.keyLastUsedProjectName)
    }

    func setLastUsedProjectName(_ name: String?) {
        setOrRemove(name, forKey: Self.keyLastUsedProjectName)
    }

    func getLastUsedSessionId() -> String? {
        defaults.string(forKey: Self.keyLastUsedSessionId)
    }

    func setLastUsedSessionId(_ id: String?) {
        setOrRemove(id, forKey: Self.keyLastUsedSessionId)
    }

    func getLastUsedClaudeOptions() -> ClaudeLaunchPrefs? {
        decodeBlob(forKey: Self.keyLastUsedClaudeOptions)
    }

    func setLastUsedClaudeOptions(_ prefs: ClaudeLaunchPrefs) {
        encodeBlob(prefs, forKey: Self.keyLastUsedClaudeOptions)
    }

    /// Auto Scan's own bucket. Kept separate from the Claude Code bucket so the
    /// two wizard modes never pre-fill each other (Android parity).
    func getLastUsedAutoScanOptions() -> ClaudeLaunchPrefs? {
        decodeBlob(forKey: Self.keyLastUsedAutoScanOptions)
    }

    func setLastUsedAutoScanOptions(_ prefs: ClaudeLaunchPrefs) {
        encodeBlob(prefs, forKey: Self.keyLastUsedAutoScanOptions)
    }

    func getLastUsedCodexOptions() -> CodexLaunchPrefs? {
        decodeBlob(forKey: Self.keyLastUsedCodexOptions)
    }

    func setLastUsedCodexOptions(_ prefs: CodexLaunchPrefs) {
        encodeBlob(prefs, forKey: Self.keyLastUsedCodexOptions)
    }

    func getLastUsedAntigravityOptions() -> AntigravityLaunchPrefs? {
        decodeBlob(forKey: Self.keyLastUsedAntigravityOptions)
    }

    func setLastUsedAntigravityOptions(_ prefs: AntigravityLaunchPrefs) {
        encodeBlob(prefs, forKey: Self.keyLastUsedAntigravityOptions)
    }

    func getTerminalDisplayMode() -> TerminalDisplayMode {
        TerminalDisplayMode(storage: defaults.string(forKey: Self.keyTerminalDisplayMode))
    }

    func setTerminalDisplayMode(_ mode: TerminalDisplayMode) {
        defaults.set(mode.rawValue, forKey: Self.keyTerminalDisplayMode)
    }

    func getTerminalStaticColumns() -> Int {
        guard let stored = defaults.object(forKey: Self.keyTerminalStaticColumns) as? Int else {
            return TerminalDisplayDefaults.staticColumns
        }
        return TerminalDisplayDefaults.clampColumns(stored)
    }

    func setTerminalStaticColumns(_ columns: Int) {
        defaults.set(TerminalDisplayDefaults.clampColumns(columns), forKey: Self.keyTerminalStaticColumns)
    }

    func getCachedLinearLabels(projectName: String) -> [LinearLabelDto] {
        decodeBlob(forKey: Self.linearLabelsKey(projectName: projectName)) ?? []
    }

    func setCachedLinearLabels(projectName: String, labels: [LinearLabelDto]) {
        encodeBlob(labels, forKey: Self.linearLabelsKey(projectName: projectName))
    }

    func getCachedLinearStates(projectName: String) -> [LinearStateDto] {
        decodeBlob(forKey: Self.linearStatesKey(projectName: projectName)) ?? []
    }

    func setCachedLinearStates(projectName: String, states: [LinearStateDto]) {
        encodeBlob(states, forKey: Self.linearStatesKey(projectName: projectName))
    }

    func getRecentCustomPaths(agentKey: String) -> [String] {
        decodeBlob(forKey: Self.recentCustomPathsKey(agentKey: agentKey)) ?? []
    }

    /// Prepend `path`, dedupe, cap at `maxRecentCustomPaths` (most-recent-first).
    func addRecentCustomPath(agentKey: String, path: String) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        var paths = getRecentCustomPaths(agentKey: agentKey).filter { $0 != trimmed }
        paths.insert(trimmed, at: 0)
        encodeBlob(
            Array(paths.prefix(Self.maxRecentCustomPaths)),
            forKey: Self.recentCustomPathsKey(agentKey: agentKey)
        )
    }

    /// Recently sent shortcut chords for one session kind, most-recent-first,
    /// as `KeyChord` tokens (Android `getRecentShortcutChords`).
    func getRecentShortcutChords(kindKey: String) -> [String] {
        decodeBlob(forKey: Self.recentShortcutChordsKey(kindKey: kindKey)) ?? []
    }

    /// Prepend `token`, dedupe, cap at `maxRecentShortcutChords` (MRU bump-to-front).
    func addRecentShortcutChord(kindKey: String, token: String) {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        var tokens = getRecentShortcutChords(kindKey: kindKey).filter { $0 != trimmed }
        tokens.insert(trimmed, at: 0)
        encodeBlob(
            Array(tokens.prefix(Self.maxRecentShortcutChords)),
            forKey: Self.recentShortcutChordsKey(kindKey: kindKey)
        )
    }

    private func setOrRemove(_ value: String?, forKey key: String) {
        if let value {
            defaults.set(value, forKey: key)
        } else {
            defaults.removeObject(forKey: key)
        }
    }

    private func decodeBlob<T: Decodable>(forKey key: String) -> T? {
        guard let raw = defaults.string(forKey: key) else { return nil }
        return try? WireJSON.decoder().decode(T.self, from: Data(raw.utf8))
    }

    private func encodeBlob<T: Encodable>(_ value: T, forKey key: String) {
        guard let encoded = try? WireJSON.encoder().encode(value) else { return }
        defaults.set(String(decoding: encoded, as: UTF8.self), forKey: key)
    }
}
