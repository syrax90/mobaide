import XCTest
@testable import Mobaide

final class PreferencesStoreTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!
    private var store: PreferencesStore!

    override func setUp() {
        super.setUp()
        suiteName = "prefs-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        store = PreferencesStore(defaults: defaults)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testLastUsedKeysRoundTripAndNilRemoves() {
        store.setLastUsedProjectName("proj")
        store.setLastUsedSessionId("s1")
        XCTAssertEqual(store.getLastUsedProjectName(), "proj")
        XCTAssertEqual(store.getLastUsedSessionId(), "s1")

        store.setLastUsedProjectName(nil)
        XCTAssertNil(store.getLastUsedProjectName())
        XCTAssertNil(defaults.object(forKey: PreferencesStore.keyLastUsedProjectName))
    }

    func testLaunchPrefsBlobsRoundTripWithDefaults() {
        XCTAssertNil(store.getLastUsedClaudeOptions())

        store.setLastUsedClaudeOptions(ClaudeLaunchPrefs(model: "opus", ultracode: true))
        let claude = store.getLastUsedClaudeOptions()
        XCTAssertEqual(claude?.model, "opus")
        XCTAssertEqual(claude?.ultracode, true)
        XCTAssertEqual(claude?.effort, "high", "Kotlin default")

        store.setLastUsedCodexOptions(CodexLaunchPrefs(fullAuto: true))
        XCTAssertEqual(store.getLastUsedCodexOptions()?.sandbox, "workspace-write")

        store.setLastUsedAntigravityOptions(AntigravityLaunchPrefs(skipPermissions: true))
        XCTAssertEqual(store.getLastUsedAntigravityOptions()?.skipPermissions, true)

        // A partial stored blob materializes Kotlin defaults on decode.
        defaults.set(#"{"model":"gpt"}"#, forKey: PreferencesStore.keyLastUsedCodexOptions)
        let partial = store.getLastUsedCodexOptions()
        XCTAssertEqual(partial?.model, "gpt")
        XCTAssertEqual(partial?.approval, "on-request")

        // A corrupt blob decodes to nil, not a crash.
        defaults.set("not-json", forKey: PreferencesStore.keyLastUsedClaudeOptions)
        XCTAssertNil(store.getLastUsedClaudeOptions())
    }

    func testClaudeLaunchPrefsRoundTripSkipPermissionsAndNoMemory() {
        store.setLastUsedClaudeOptions(ClaudeLaunchPrefs(skipPermissions: true, noMemory: true))
        let saved = store.getLastUsedClaudeOptions()
        XCTAssertEqual(saved?.skipPermissions, true)
        XCTAssertEqual(saved?.noMemory, true)

        // A blob persisted before the toggle fields existed must decode and
        // materialize them false — not fail and reset every other pref.
        defaults.set(
            #"{"model":"opus","effort":"max","fastMode":true,"ultracode":false,"appendSystemPrompt":"sys"}"#,
            forKey: PreferencesStore.keyLastUsedClaudeOptions
        )
        let legacy = store.getLastUsedClaudeOptions()
        XCTAssertEqual(legacy?.model, "opus")
        XCTAssertEqual(legacy?.fastMode, true)
        XCTAssertEqual(legacy?.skipPermissions, false)
        XCTAssertEqual(legacy?.noMemory, false)
    }

    func testDynamicKeysUseDoubleColonSeparator() {
        XCTAssertEqual(PreferencesStore.linearLabelsKey(projectName: "p"), "linear_labels::p")
        XCTAssertEqual(PreferencesStore.linearStatesKey(projectName: "p"), "linear_states::p")
        XCTAssertEqual(PreferencesStore.recentCustomPathsKey(agentKey: "k"), "recent_custom_paths::k")
    }

    func testRecentCustomPathsPrependDedupeAndCap() {
        for path in ["/one", "/two", "/three"] {
            store.addRecentCustomPath(agentKey: "claude", path: path)
        }
        XCTAssertEqual(store.getRecentCustomPaths(agentKey: "claude"), ["/three", "/two", "/one"])

        store.addRecentCustomPath(agentKey: "claude", path: "/one")
        XCTAssertEqual(
            store.getRecentCustomPaths(agentKey: "claude"),
            ["/one", "/three", "/two"],
            "re-adding moves to front without duplicating"
        )

        for path in ["/four", "/five", "/six"] {
            store.addRecentCustomPath(agentKey: "claude", path: path)
        }
        XCTAssertEqual(store.getRecentCustomPaths(agentKey: "claude").count, 5, "cap at 5")
        XCTAssertEqual(store.getRecentCustomPaths(agentKey: "claude").first, "/six")

        store.addRecentCustomPath(agentKey: "claude", path: "   ")
        XCTAssertEqual(store.getRecentCustomPaths(agentKey: "claude").count, 5, "blank paths ignored")

        XCTAssertEqual(store.getRecentCustomPaths(agentKey: "codex"), [], "per-agent isolation")
    }

    func testRecentShortcutChordsMRUOrderBumpAndCap() {
        XCTAssertEqual(
            PreferencesStore.recentShortcutChordsKey(kindKey: "claude"),
            "recent_shortcut_chords::claude"
        )

        for token in ["ctrl+a", "ctrl+b", "ctrl+c"] {
            store.addRecentShortcutChord(kindKey: "claude", token: token)
        }
        XCTAssertEqual(store.getRecentShortcutChords(kindKey: "claude"), ["ctrl+c", "ctrl+b", "ctrl+a"])

        store.addRecentShortcutChord(kindKey: "claude", token: "ctrl+a")
        XCTAssertEqual(
            store.getRecentShortcutChords(kindKey: "claude"),
            ["ctrl+a", "ctrl+c", "ctrl+b"],
            "re-sending a chord bumps it to the front without duplicating"
        )

        let tokens = "abcdefghijk".map { "ctrl+\($0)" }
        XCTAssertEqual(tokens.count, PreferencesStore.maxRecentShortcutChords + 1)
        for token in tokens {
            store.addRecentShortcutChord(kindKey: "shell", token: token)
        }
        let capped = store.getRecentShortcutChords(kindKey: "shell")
        XCTAssertEqual(capped.count, PreferencesStore.maxRecentShortcutChords, "cap at 10")
        XCTAssertEqual(
            capped, Array(tokens.dropFirst().reversed()),
            "the oldest chord falls off once the cap is exceeded"
        )
    }

    func testRecentShortcutChordsIgnoreBlanksIsolateBucketsAndSurviveGarbage() {
        store.addRecentShortcutChord(kindKey: "claude", token: "")
        store.addRecentShortcutChord(kindKey: "claude", token: "   ")
        XCTAssertEqual(store.getRecentShortcutChords(kindKey: "claude"), [], "blank tokens are ignored")

        store.addRecentShortcutChord(kindKey: "claude", token: "ctrl+x")
        store.addRecentShortcutChord(kindKey: "codex", token: "ctrl+y")
        XCTAssertEqual(store.getRecentShortcutChords(kindKey: "claude"), ["ctrl+x"])
        XCTAssertEqual(store.getRecentShortcutChords(kindKey: "codex"), ["ctrl+y"], "per-kind buckets are isolated")
        XCTAssertEqual(store.getRecentShortcutChords(kindKey: "auto_scan"), [], "untouched bucket reads empty")

        defaults.set("not-json", forKey: PreferencesStore.recentShortcutChordsKey(kindKey: "claude"))
        XCTAssertEqual(
            store.getRecentShortcutChords(kindKey: "claude"), [],
            "a corrupt blob decodes to empty, not a crash"
        )
    }

    func testTerminalDisplayModeDefaultsToStaticAndPersistsRawValue() {
        XCTAssertEqual(store.getTerminalDisplayMode(), .static, "unset default matches Android")

        store.setTerminalDisplayMode(.full)
        XCTAssertEqual(store.getTerminalDisplayMode(), .full)
        XCTAssertEqual(
            defaults.string(forKey: PreferencesStore.keyTerminalDisplayMode), "full",
            "persists the raw string, not an ordinal"
        )

        store.setTerminalDisplayMode(.static)
        XCTAssertEqual(store.getTerminalDisplayMode(), .static)
    }

    func testTerminalStaticColumnsDefaultRoundTripAndClampOnBothPaths() {
        XCTAssertEqual(store.getTerminalStaticColumns(), 120, "unset default")

        store.setTerminalStaticColumns(200)
        XCTAssertEqual(store.getTerminalStaticColumns(), 200, "in-range value round-trips")

        store.setTerminalStaticColumns(10)
        XCTAssertEqual(store.getTerminalStaticColumns(), 60, "write path clamps below-min up to 60")

        store.setTerminalStaticColumns(5000)
        XCTAssertEqual(store.getTerminalStaticColumns(), 240, "write path clamps above-max down to 240")

        defaults.set(9999, forKey: PreferencesStore.keyTerminalStaticColumns)
        XCTAssertEqual(store.getTerminalStaticColumns(), 240, "read path clamps a raw out-of-range stored value")
    }
}
