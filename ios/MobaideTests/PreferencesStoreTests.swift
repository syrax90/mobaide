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

    func testAutoScanBucketRoundTripsPlanModeAndStaysIsolatedFromClaude() {
        XCTAssertNil(store.getLastUsedAutoScanOptions())

        store.setLastUsedClaudeOptions(ClaudeLaunchPrefs(model: "sonnet", skipPermissions: false))
        store.setLastUsedAutoScanOptions(ClaudeLaunchPrefs(model: "opus", skipPermissions: true, planMode: true))

        let autoScan = store.getLastUsedAutoScanOptions()
        XCTAssertEqual(autoScan?.model, "opus")
        XCTAssertEqual(autoScan?.skipPermissions, true)
        XCTAssertEqual(autoScan?.planMode, true)

        let claude = store.getLastUsedClaudeOptions()
        XCTAssertEqual(claude?.model, "sonnet", "the two wizard modes never pre-fill each other")
        XCTAssertEqual(claude?.skipPermissions, false)
        XCTAssertEqual(claude?.planMode, false, "planMode is meaningful only in the Auto Scan bucket")

        // A blob persisted before planMode existed decodes it false rather than
        // failing and resetting the whole bucket.
        defaults.set(
            #"{"model":"haiku","effort":"low","skipPermissions":true}"#,
            forKey: PreferencesStore.keyLastUsedAutoScanOptions
        )
        let legacy = store.getLastUsedAutoScanOptions()
        XCTAssertEqual(legacy?.model, "haiku")
        XCTAssertEqual(legacy?.skipPermissions, true)
        XCTAssertEqual(legacy?.planMode, false)
    }

    func testDynamicKeysUseDoubleColonSeparator() {
        XCTAssertEqual(PreferencesStore.linearLabelsKey(projectName: "p"), "linear_labels::p")
        XCTAssertEqual(PreferencesStore.linearStatesKey(projectName: "p"), "linear_states::p")
        XCTAssertEqual(PreferencesStore.recentCustomPathsKey(agentKey: "k"), "recent_custom_paths::k")
        XCTAssertEqual(PreferencesStore.attentionSeenKey(agentKey: "k"), "attention_seen::k")
    }

    func testAttentionSeenBucketsRoundTripPerAgentAndSurviveGarbage() {
        XCTAssertEqual(store.getAttentionSeen(agentKey: "a1"), AttentionSeenRecord())

        store.setAttentionSeen(
            agentKey: "a1",
            record: AttentionSeenRecord(baselined: true, seen: ["s1": "turn_finished|"])
        )
        let saved = store.getAttentionSeen(agentKey: "a1")
        XCTAssertEqual(saved.baselined, true)
        XCTAssertEqual(saved.seen, ["s1": "turn_finished|"])
        XCTAssertEqual(
            store.getAttentionSeen(agentKey: "a2"), AttentionSeenRecord(),
            "per-agent isolation"
        )

        defaults.set("not-json", forKey: PreferencesStore.attentionSeenKey(agentKey: "a1"))
        XCTAssertEqual(
            store.getAttentionSeen(agentKey: "a1"), AttentionSeenRecord(),
            "a corrupt blob decodes to an unseeded record, not a crash"
        )
    }

    func testRemoveAllAttentionSeenClearsEveryAgentAndNothingElse() {
        // Sign-out clears by prefix because the current agent key may already
        // be gone — that scan must not take unrelated preferences with it.
        store.setAttentionSeen(agentKey: "a1", record: AttentionSeenRecord(baselined: true))
        store.setAttentionSeen(agentKey: "a2", record: AttentionSeenRecord(baselined: true))
        store.setLastUsedProjectName("proj")
        store.addRecentCustomPath(agentKey: "a1", path: "/one")

        store.removeAllAttentionSeen()

        XCTAssertEqual(store.getAttentionSeen(agentKey: "a1"), AttentionSeenRecord())
        XCTAssertEqual(store.getAttentionSeen(agentKey: "a2"), AttentionSeenRecord())
        XCTAssertEqual(store.getLastUsedProjectName(), "proj")
        XCTAssertEqual(store.getRecentCustomPaths(agentKey: "a1"), ["/one"])
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

    func testRemoveRecentCustomPathDropsOnlyTheExactMatch() {
        for path in ["/one", "/two", "/three"] {
            store.addRecentCustomPath(agentKey: "claude", path: path)
        }
        store.addRecentCustomPath(agentKey: "codex", path: "/two")

        store.removeRecentCustomPath(agentKey: "claude", path: "/tw")
        XCTAssertEqual(
            store.getRecentCustomPaths(agentKey: "claude"), ["/three", "/two", "/one"],
            "a path that is only a prefix of a stored one removes nothing"
        )

        store.removeRecentCustomPath(agentKey: "claude", path: "  /two  ")
        XCTAssertEqual(
            store.getRecentCustomPaths(agentKey: "claude"), ["/three", "/one"],
            "removal trims like the add path and leaves the survivors in MRU order"
        )
        XCTAssertEqual(store.getRecentCustomPaths(agentKey: "codex"), ["/two"], "per-agent isolation")

        store.removeRecentCustomPath(agentKey: "claude", path: "/three")
        store.removeRecentCustomPath(agentKey: "claude", path: "/one")
        XCTAssertEqual(
            store.getRecentCustomPaths(agentKey: "claude"), [],
            "removing the last entry empties the bucket"
        )
        XCTAssertNil(
            defaults.dictionaryRepresentation()[PreferencesStore.recentCustomPathsKey(agentKey: "claude")],
            "the emptied bucket is deleted, not parked as an \"[]\" blob keyed by an agent URL that may never return"
        )
        XCTAssertNotNil(
            defaults.dictionaryRepresentation()[PreferencesStore.recentCustomPathsKey(agentKey: "codex")],
            "and the delete is scoped to the one agent"
        )
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
