import XCTest
@testable import Mobaide

/// `TerminalSessionModel` custom-shortcut paths — `sendChord` + recents MRU,
/// the kind-gated `savedShortcutsForKind` projection, and the
/// save/rename/remove actions over `ShortcutsRepository`. Mirrors Android
/// `TerminalViewModelSendChordTest` + `TerminalViewModelSavedShortcutsTest`.
///
/// Load-bearing contracts: chords ride the raw `i` frame; recents key on the
/// resolved kind's wire name and fall back to the render seed; the saved list
/// stays empty until the REAL kind is known (the eagerly-seeded `.claude`
/// default must never leak); saves resolve the kind and publish it, are always
/// unnamed, and drop a duplicate while one is in flight — but the per-chord
/// guard always releases; an already-gone delete (403/404) prunes silently.
@MainActor
final class TerminalSessionModelShortcutsTests: XCTestCase {

    private final class KindResolverBox: @unchecked Sendable {
        private let lock = NSLock()
        private var kindStorage: SessionKind?
        var kind: SessionKind? {
            get { lock.withLock { kindStorage } }
            set { lock.withLock { kindStorage = newValue } }
        }
    }

    private struct Harness {
        let factory: FakeWebSocketTransportFactory
        let prefs: PreferencesStore
        let repo: ShortcutsRepository
        let resolver: KindResolverBox
        let model: TerminalSessionModel
    }

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeHarness(
        kind: SessionKind = .claude,
        resolvedKind: SessionKind? = nil,
        resolverKind: SessionKind? = nil
    ) -> Harness {
        let factory = FakeWebSocketTransportFactory()
        let session = SessionHarness(userLoggedIn: true, agentConnected: true)
        let prefs = TerminalTestSupport.isolatedPreferencesStore()
        let repo = TerminalTestSupport.shortcutsRepository(harness: session)
        let resolver = KindResolverBox()
        resolver.kind = resolverKind
        let model = TerminalTestSupport.model(
            kind: kind,
            resolvedKind: resolvedKind,
            transportFactory: factory,
            harness: session,
            preferencesStore: prefs,
            shortcutsRepository: repo,
            kindResolver: { _ in resolver.kind }
        )
        return Harness(factory: factory, prefs: prefs, repo: repo, resolver: resolver, model: model)
    }

    private func connect(_ harness: Harness) async throws -> FakeWebSocketConnection {
        harness.model.start()
        let connection = try await harness.factory.connection(1)
        connection.emit(.opened)
        return connection
    }

    private func waitForBinaries(_ connection: FakeWebSocketConnection, atLeast count: Int) async -> [Data] {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while ContinuousClock.now < deadline {
            if connection.sentBinaries.count >= count { return connection.sentBinaries }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return connection.sentBinaries
    }

    private func requestBody(_ index: Int) throws -> JSONValue {
        try WireJSON.decoder().decode(JSONValue.self, from: StubURLProtocol.bodies[index])
    }

    // MARK: - sendChord + recents

    func testSendChordSendsEncodedBytesAsRawInputFrame() async throws {
        let harness = makeHarness()
        let connection = try await connect(harness)

        harness.model.sendChord(KeyChord(ctrl: true, key: "a"))

        let sent = await waitForBinaries(connection, atLeast: 1)
        XCTAssertEqual(
            sent.first, Data([0x69, 0x01]),
            "chords must use the raw 'i' (input) frame with the encoded byte, never the submit path"
        )
    }

    func testSendChordPersistsTokenUnderWireBucketAndReloadsRecents() {
        let harness = makeHarness(kind: .claude)

        harness.model.sendChord(KeyChord(ctrl: true, key: "a"))

        XCTAssertEqual(harness.prefs.getRecentShortcutChords(kindKey: "claude"), ["ctrl+a"])
        XCTAssertEqual(
            harness.model.shortcutRecents, [KeyChord(ctrl: true, key: "a")],
            "a sent chord must reappear in recents without reopening the sheet"
        )
    }

    func testSendChordPrefersResolvedKindBucketOverRenderSeed() {
        let harness = makeHarness(kind: .claude, resolvedKind: .codex)

        harness.model.sendChord(KeyChord(ctrl: true, key: "x"))

        XCTAssertEqual(harness.prefs.getRecentShortcutChords(kindKey: "codex"), ["ctrl+x"])
        XCTAssertEqual(
            harness.prefs.getRecentShortcutChords(kindKey: "claude"), [],
            "once the real kind is known the seed bucket must not be touched"
        )
    }

    func testSendChordUnencodableSendsNothingAndPersistsNothing() async throws {
        let harness = makeHarness()
        let connection = try await connect(harness)

        harness.model.sendChord(KeyChord(ctrl: true, alt: true, shift: true, key: "a"))

        try await Task.sleep(for: .milliseconds(100))
        XCTAssertTrue(connection.sentBinaries.isEmpty)
        XCTAssertEqual(harness.prefs.getRecentShortcutChords(kindKey: "claude"), [])
        XCTAssertTrue(harness.model.shortcutRecents.isEmpty)
    }

    func testRefreshShortcutRecentsDropsUnparseableTokens() {
        let harness = makeHarness(kind: .claude)
        for token in ["shift+f5", "bogus", "ctrl+alt+shift+a", "meta+x", "ctrl+a"] {
            harness.prefs.addRecentShortcutChord(kindKey: "claude", token: token)
        }

        harness.model.refreshShortcutRecents()

        XCTAssertEqual(
            harness.model.shortcutRecents,
            [KeyChord(ctrl: true, key: "a"), KeyChord(shift: true, key: "f5")],
            "stale or forward-incompatible tokens must be dropped silently"
        )
    }

    func testShortcutRecentsEmptyBeforeFirstRefresh() {
        XCTAssertTrue(makeHarness().model.shortcutRecents.isEmpty)
    }

    // MARK: - savedShortcutsForKind

    func testSavedShortcutsFilterByKindDropUnparseableAndMapFields() async {
        let harness = makeHarness(resolvedKind: .claude)
        await harness.repo.applyUpserted(makeShortcut(id: "s-b", chordToken: "ctrl+b", name: "   ", position: 1))
        await harness.repo.applyUpserted(makeShortcut(id: "s-a", chordToken: "ctrl+a", name: "Alpha", position: 0))
        await harness.repo.applyUpserted(makeShortcut(id: "s-codex", kind: "codex", chordToken: "ctrl+x", position: 0))
        await harness.repo.applyUpserted(makeShortcut(id: "s-bad", chordToken: "meta+q", position: 2))
        await harness.repo.applyUpserted(makeShortcut(id: "s-risky", chordToken: "ctrl+shift+i", position: 3))
        harness.model.start()

        await waitUntil { harness.model.savedShortcutsForKind.count == 3 }
        XCTAssertEqual(harness.model.savedShortcutsForKind, [
            SavedShortcutUi(id: "s-a", chord: KeyChord(ctrl: true, key: "a"), name: "Alpha", risky: false),
            // Blank server name collapses to nil so the label falls back to
            // the chord display.
            SavedShortcutUi(id: "s-b", chord: KeyChord(ctrl: true, key: "b"), name: nil, risky: false),
            // CSI-u chord: riskiness is re-derived from the local encoder.
            SavedShortcutUi(id: "s-risky", chord: KeyChord(ctrl: true, shift: true, key: "i"), name: nil, risky: true),
        ])
    }

    func testSavedShortcutsSortByPositionThenCreatedAtThenId() async {
        let harness = makeHarness(resolvedKind: .claude)
        await harness.repo.applyUpserted(makeShortcut(id: "s-z", chordToken: "ctrl+z", position: 0, createdAt: "2026-01-01T00:00:00Z"))
        await harness.repo.applyUpserted(makeShortcut(id: "s-y", chordToken: "ctrl+y", position: 0, createdAt: "2026-01-02T00:00:00Z"))
        await harness.repo.applyUpserted(makeShortcut(id: "s-a", chordToken: "ctrl+a", position: 0, createdAt: "2026-01-01T00:00:00Z"))
        await harness.repo.applyUpserted(makeShortcut(id: "s-first", chordToken: "ctrl+f", position: 1, createdAt: "2025-01-01T00:00:00Z"))
        harness.model.start()

        await waitUntil { harness.model.savedShortcutsForKind.count == 4 }
        XCTAssertEqual(
            harness.model.savedShortcutsForKind.map(\.id),
            ["s-a", "s-z", "s-y", "s-first"]
        )
    }

    func testSavedShortcutsGatedEmptyUntilKindResolves() async {
        // The render seed is .claude, but the SEED must never leak into the
        // saved-button row while the real kind is unknown.
        let harness = makeHarness(kind: .claude, resolvedKind: nil, resolverKind: nil)
        await harness.repo.applyUpserted(makeShortcut(id: "s-a", chordToken: "ctrl+a", name: "Alpha"))
        harness.model.start()

        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(harness.model.savedShortcutsForKind, [], "unresolved kind keeps the list gated empty")

        harness.model.adoptResolvedKind(.claude)
        await waitUntil { harness.model.savedShortcutsForKind.count == 1 }
        XCTAssertEqual(harness.model.savedShortcutsForKind.map(\.id), ["s-a"])
    }

    // MARK: - refreshSavedShortcuts

    func testRefreshSavedShortcutsSingleFlightAndGuardReleases() async {
        let harness = makeHarness()
        StubURLProtocol.enqueue(status: 200, body: #"[{"shortcut_id":"s-1","kind":"claude","chord_token":"ctrl+a","position":0,"created_at":"c","updated_at":"u","version":1}]"#)

        harness.model.refreshSavedShortcuts()
        harness.model.refreshSavedShortcuts()

        await waitUntil { harness.repo.currentShortcuts.count == 1 }
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(StubURLProtocol.requests.count, 1, "the second call while in flight must not start a second fetch")

        StubURLProtocol.enqueue(status: 200, body: "[]")
        harness.model.refreshSavedShortcuts()
        await waitUntil { harness.repo.currentShortcuts.isEmpty }
        XCTAssertEqual(StubURLProtocol.requests.count, 2, "the guard must release so the next open refetches")
    }

    func testRefreshSavedShortcutsFailureSetsToastAndGuardDoesNotLatch() async {
        let harness = makeHarness()
        StubURLProtocol.enqueue(status: 500, body: "boom")

        harness.model.refreshSavedShortcuts()
        await waitUntil { harness.model.shortcutMessage != nil }
        XCTAssertEqual(harness.model.shortcutMessage?.text, "Failed to load saved shortcuts")

        StubURLProtocol.enqueue(status: 200, body: #"[{"shortcut_id":"s-1","kind":"claude","chord_token":"ctrl+a","position":0,"created_at":"c","updated_at":"u","version":1}]"#)
        harness.model.refreshSavedShortcuts()
        await waitUntil { harness.repo.currentShortcuts.count == 1 }
        XCTAssertEqual(harness.repo.currentShortcuts.map(\.shortcut_id), ["s-1"], "a failed fetch must not latch the guard")
    }

    // MARK: - saveShortcut

    func testSaveShortcutPostsResolverKindAndTokenUnnamedAndPublishesKind() async throws {
        let harness = makeHarness(kind: .claude, resolvedKind: nil, resolverKind: .codex)
        StubURLProtocol.enqueue(status: 201, body: #"{"shortcut_id":"s-1","kind":"codex","chord_token":"ctrl+x","position":0,"created_at":"c","updated_at":"u","version":1}"#)

        harness.model.saveShortcut(KeyChord(ctrl: true, key: "x"))

        await waitUntil { StubURLProtocol.requests.count == 1 }
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString, "https://central.test/api/shortcuts")
        let body = try requestBody(0)
        XCTAssertEqual(
            body, ["kind": "codex", "chord_token": "ctrl+x"],
            "saves go out under the RESOLVED kind, always unnamed (name omitted, not null)"
        )
        XCTAssertEqual(harness.model.resolvedKind, .codex, "a save that resolves the kind publishes it")
    }

    func testSaveShortcutUnresolvableKindAbortsWithMessageAndReleasesGuard() async {
        let harness = makeHarness(kind: .claude, resolvedKind: nil, resolverKind: nil)

        harness.model.saveShortcut(KeyChord(ctrl: true, key: "x"))

        await waitUntil { harness.model.shortcutMessage != nil }
        XCTAssertEqual(harness.model.shortcutMessage?.text, "Failed to save shortcut: session type not known yet")
        XCTAssertTrue(StubURLProtocol.requests.isEmpty, "the repository must never be touched with an unresolved kind")
        XCTAssertNil(harness.model.resolvedKind, "an aborted save must not open the gate with the seeded default")

        // The abort path must release the per-chord guard so the retry the
        // message invites is possible.
        harness.resolver.kind = .claude
        StubURLProtocol.enqueue(status: 201, body: #"{"shortcut_id":"s-1","kind":"claude","chord_token":"ctrl+x","position":0,"created_at":"c","updated_at":"u","version":1}"#)
        harness.model.saveShortcut(KeyChord(ctrl: true, key: "x"))
        await waitUntil { StubURLProtocol.requests.count == 1 }
        XCTAssertEqual(StubURLProtocol.requests.count, 1)
    }

    func testSaveShortcutDuplicateWhileInFlightIsDroppedAndGuardReleases() async {
        let harness = makeHarness(resolvedKind: .claude)
        StubURLProtocol.enqueue(status: 201, body: #"{"shortcut_id":"s-1","kind":"claude","chord_token":"ctrl+x","position":0,"created_at":"c","updated_at":"u","version":1}"#)
        let chord = KeyChord(ctrl: true, key: "x")

        // The guard is taken synchronously before the task runs, so the second
        // call here is deterministically the in-flight duplicate.
        harness.model.saveShortcut(chord)
        harness.model.saveShortcut(chord)

        await waitUntil { harness.repo.currentShortcuts.count == 1 }
        try? await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(
            StubURLProtocol.requests.count, 1,
            "a duplicate POST after an interleaved delete would resurrect the removed chord"
        )

        StubURLProtocol.enqueue(status: 201, body: #"{"shortcut_id":"s-1","kind":"claude","chord_token":"ctrl+x","position":0,"created_at":"c","updated_at":"u","version":1}"#)
        harness.model.saveShortcut(chord)
        await waitUntil { StubURLProtocol.requests.count == 2 }
        XCTAssertEqual(StubURLProtocol.requests.count, 2, "a latched guard would make the chord permanently unsavable")
    }

    func testSaveShortcutDifferentChordsBothReachTheServer() async throws {
        let harness = makeHarness(resolvedKind: .claude)
        StubURLProtocol.enqueue(status: 201, body: #"{"shortcut_id":"s-1","kind":"claude","chord_token":"ctrl+x","position":0,"created_at":"c","updated_at":"u","version":1}"#)
        StubURLProtocol.enqueue(status: 201, body: #"{"shortcut_id":"s-2","kind":"claude","chord_token":"ctrl+y","position":1,"created_at":"c","updated_at":"u","version":1}"#)

        harness.model.saveShortcut(KeyChord(ctrl: true, key: "x"))
        harness.model.saveShortcut(KeyChord(ctrl: true, key: "y"))

        await waitUntil { StubURLProtocol.requests.count == 2 }
        let tokens = try Set([requestBody(0), requestBody(1)].map { try XCTUnwrap($0["chord_token"]) })
        XCTAssertEqual(tokens, ["ctrl+x", "ctrl+y"], "the guard is per chord token, not a global flag")
    }

    func testSaveShortcutFailureSetsToastAndGuardReleases() async {
        let harness = makeHarness(resolvedKind: .claude)
        StubURLProtocol.enqueue(status: 500, body: "boom")
        let chord = KeyChord(ctrl: true, key: "x")

        harness.model.saveShortcut(chord)
        await waitUntil { harness.model.shortcutMessage != nil }
        XCTAssertEqual(harness.model.shortcutMessage?.text, "Failed to save shortcut")

        StubURLProtocol.enqueue(status: 201, body: #"{"shortcut_id":"s-1","kind":"claude","chord_token":"ctrl+x","position":0,"created_at":"c","updated_at":"u","version":1}"#)
        harness.model.saveShortcut(chord)
        await waitUntil { StubURLProtocol.requests.count == 2 }
        XCTAssertEqual(StubURLProtocol.requests.count, 2, "a failed save leaves the chord savable")
    }

    // MARK: - renameShortcut

    func testRenameShortcutTrimsNameAndPatches() async throws {
        let harness = makeHarness()
        StubURLProtocol.enqueue(status: 200, body: #"{"shortcut_id":"s-1","kind":"claude","chord_token":"ctrl+x","name":"New","position":0,"created_at":"c","updated_at":"u","version":2}"#)

        harness.model.renameShortcut(id: "s-1", name: "  New  ")

        await waitUntil { StubURLProtocol.requests.count == 1 }
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "PATCH")
        XCTAssertEqual(request.url?.absoluteString, "https://central.test/api/shortcuts/s-1")
        XCTAssertEqual(try requestBody(0), ["name": "New"])
    }

    func testRenameShortcutBlankNameSendsEmptyStringToClear() async throws {
        let harness = makeHarness()
        StubURLProtocol.enqueue(status: 200, body: #"{"shortcut_id":"s-1","kind":"claude","chord_token":"ctrl+x","position":0,"created_at":"c","updated_at":"u","version":2}"#)

        harness.model.renameShortcut(id: "s-1", name: "   ")

        await waitUntil { StubURLProtocol.requests.count == 1 }
        XCTAssertEqual(
            try requestBody(0), ["name": ""],
            "blank collapses to the wire sentinel that clears the custom name"
        )
    }

    func testRenameShortcutFailureSetsToast() async {
        let harness = makeHarness()
        StubURLProtocol.enqueue(status: 500, body: "boom")

        harness.model.renameShortcut(id: "s-1", name: "New")

        await waitUntil { harness.model.shortcutMessage != nil }
        XCTAssertEqual(harness.model.shortcutMessage?.text, "Failed to rename shortcut")
    }

    // MARK: - removeShortcut

    func testRemoveShortcutDeletesAndPrunesCacheSilently() async throws {
        let harness = makeHarness()
        await harness.repo.applyUpserted(makeShortcut(id: "s-1", chordToken: "ctrl+a"))
        StubURLProtocol.enqueue(status: 204, body: "")

        harness.model.removeShortcut(id: "s-1")

        await waitUntil { harness.repo.currentShortcuts.isEmpty }
        let request = try XCTUnwrap(StubURLProtocol.requests.first)
        XCTAssertEqual(request.httpMethod, "DELETE")
        XCTAssertEqual(request.url?.absoluteString, "https://central.test/api/shortcuts/s-1")
        XCTAssertNil(harness.model.shortcutMessage)
    }

    func testRemoveShortcutAlreadyGone403PrunesSilently() async {
        await assertAlreadyGoneIsAbsorbed(status: 403)
    }

    func testRemoveShortcutAlreadyGone404PrunesSilently() async {
        await assertAlreadyGoneIsAbsorbed(status: 404)
    }

    /// DELETE is not idempotent server-side: an already-soft-deleted row
    /// answers 403 (404 when never visible). Ids only ever come from the
    /// user's own cached list, so these codes can only mean "already gone" —
    /// absorb them into a silent cache prune instead of a false failure toast.
    private func assertAlreadyGoneIsAbsorbed(status: Int) async {
        let harness = makeHarness()
        await harness.repo.applyUpserted(makeShortcut(id: "s-1", chordToken: "ctrl+a"))
        StubURLProtocol.enqueue(status: status, body: "not the owner of this shortcut")

        harness.model.removeShortcut(id: "s-1")

        await waitUntil { harness.repo.currentShortcuts.isEmpty }
        XCTAssertTrue(harness.repo.currentShortcuts.isEmpty, "\(status) must reconcile the cache the failed call never pruned")
        XCTAssertNil(harness.model.shortcutMessage, "\(status) is not a user-visible failure")
    }

    func testRemoveShortcutOtherErrorSetsToastAndKeepsCache() async {
        let harness = makeHarness()
        await harness.repo.applyUpserted(makeShortcut(id: "s-1", chordToken: "ctrl+a"))
        StubURLProtocol.enqueue(status: 500, body: "internal server error")

        harness.model.removeShortcut(id: "s-1")

        await waitUntil { harness.model.shortcutMessage != nil }
        XCTAssertEqual(harness.model.shortcutMessage?.text, "Failed to remove shortcut")
        XCTAssertEqual(
            harness.repo.currentShortcuts.map(\.shortcut_id), ["s-1"],
            "a 500 says nothing about the row's fate — the cache must be left alone"
        )
    }
}
