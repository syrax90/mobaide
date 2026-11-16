import UserNotifications
import XCTest
@testable import Mobaide

/// The alert of an agent in a Herdr pane, from the wire to the tap. A pane has
/// no session until a client asks for one, so the alert is identified, shown,
/// suppressed and opened by server and pane, never by a session id.
@MainActor
final class HerdrAttentionTests: XCTestCase {
    private func attention(
        paneId: String = "w1:p1",
        reason: String = "awaiting_permission",
        title: String = "api refactor"
    ) -> HerdrAttention {
        HerdrAttention(server_id: "tui:default", pane_id: paneId, reason: reason, title: title)
    }

    private func snapshot(_ panes: [HerdrPane]) -> HerdrSnapshot {
        HerdrSnapshot(servers: [
            HerdrServer(
                server_id: "tui:default",
                workspaces: [HerdrWorkspace(workspace_id: "w1", tabs: [HerdrTab(tab_id: "w1:t1", panes: panes)])]
            ),
        ])
    }

    // MARK: - Request

    func testRequestCarriesThePaneKeysTheTapReads() throws {
        let request = try XCTUnwrap(NotificationService.makeRequest(for: attention(paneId: "box/t3")))

        XCTAssertEqual(request.content.title, "Permission needed")
        XCTAssertEqual(request.content.body, "api refactor is waiting for your approval")
        XCTAssertEqual(request.content.threadIdentifier, request.identifier)
        let pane = try XCTUnwrap(NotificationService.herdrPane(from: request.content.userInfo))
        XCTAssertEqual(pane.serverId, "tui:default")
        XCTAssertEqual(pane.paneId, "box/t3")
    }

    func testRequestIdentifierIsOnePerPaneAndIsNeverASessionId() throws {
        let first = try XCTUnwrap(NotificationService.makeRequest(for: attention(paneId: "w1:p1")))
        let again = try XCTUnwrap(NotificationService.makeRequest(for: attention(paneId: "w1:p1", reason: "turn_failed")))
        let other = try XCTUnwrap(NotificationService.makeRequest(for: attention(paneId: "w1:p2")))

        // The same pane replaces its own alert; another pane stands beside it.
        XCTAssertEqual(first.identifier, again.identifier)
        XCTAssertNotEqual(first.identifier, other.identifier)
        // The session reconcile removes every delivered id that no session
        // explains; the prefix is what keeps a Herdr alert out of it.
        XCTAssertTrue(first.identifier.hasPrefix(NotificationService.herdrIdentifierPrefix))
        XCTAssertEqual(
            first.identifier,
            NotificationService.herdrIdentifier(serverId: "tui:default", paneId: "w1:p1")
        )
    }

    func testRequestNilForAReasonThisBuildDoesNotKnow() {
        XCTAssertNil(NotificationService.makeRequest(for: attention(reason: "future_reason")))
    }

    func testAPaneAlertReadsAsTheSessionAlertOfTheSameReason() throws {
        // One text table for sessions and panes: a reason must not read one
        // way for a session and another way for the same agent in a pane.
        for reason in ["awaiting_permission", "turn_finished", "turn_failed"] {
            let forSession = try XCTUnwrap(
                AttentionContent.content(for: makeSessionResponse(attention: reason, title: "api refactor")), reason
            )
            let forPane = try XCTUnwrap(
                AttentionContent.content(for: attention(reason: reason, title: "api refactor")), reason
            )

            XCTAssertEqual(forPane.title, forSession.title, reason)
            XCTAssertEqual(forPane.body, forSession.body, reason)
            XCTAssertTrue(forPane.body.hasPrefix("api refactor "), reason)
        }
    }

    func testUntitledPaneUsesTheLabelOfThePush() {
        // The server names an untitled pane "Herdr agent" in its push.
        let content = AttentionContent.content(for: attention(reason: "turn_failed", title: " "))

        XCTAssertEqual(content?.body, "Herdr agent hit an error — tap to see what happened")
    }

    func testHerdrPaneReadFromTheRemotePayloadAndNilForASessionAlert() {
        // The relay's APNs payload: `herdr_server_id`, `herdr_pane_id`, `agent_id`.
        let remote: [AnyHashable: Any] = [
            "herdr_server_id": "core:default", "herdr_pane_id": "box/t3", "agent_id": "agent-1",
        ]
        let pane = NotificationService.herdrPane(from: remote)

        XCTAssertEqual(pane?.serverId, "core:default")
        XCTAssertEqual(pane?.paneId, "box/t3")
        XCTAssertEqual(NotificationService.agentId(from: remote), "agent-1")
        XCTAssertNil(NotificationService.herdrPane(from: ["session_id": "s1", "agent_id": "agent-1"]))
        XCTAssertNil(NotificationService.herdrPane(from: ["herdr_server_id": "core:default"]))
        XCTAssertNil(NotificationService.herdrPane(from: ["herdr_server_id": "core:default", "herdr_pane_id": " "]))
        XCTAssertNil(NotificationService.herdrPane(from: ["herdr_server_id": 7, "herdr_pane_id": "box/t3"]))
    }

    // MARK: - Suppression

    func testOnlyThePaneOnScreenIsSuppressed() {
        let tracker = ForegroundSessionTracker()
        let onScreen = ForegroundSessionTracker.herdrPaneKey(serverId: "tui:default", paneId: "w1:p1")
        let other = ForegroundSessionTracker.herdrPaneKey(serverId: "tui:default", paneId: "w1:p2")
        let otherServer = ForegroundSessionTracker.herdrPaneKey(serverId: "tui:work", paneId: "w1:p1")
        tracker.setVisibleHerdrPane(onScreen)

        func suppressed(_ key: String, active: Bool = true) -> Bool {
            AttentionSuppression.shouldSuppress(
                appActive: active, visibleSessionId: tracker.visibleHerdrPane, eventSessionId: key
            )
        }
        XCTAssertTrue(suppressed(onScreen))
        XCTAssertFalse(suppressed(other))
        XCTAssertFalse(suppressed(otherServer))
        XCTAssertFalse(suppressed(onScreen, active: false))

        // A screen that already left must not clear its successor's pane.
        tracker.setVisibleHerdrPane(other)
        tracker.clearVisibleHerdrPane(onScreen)
        XCTAssertEqual(tracker.visibleHerdrPane, other)
        tracker.clearVisibleHerdrPane(other)
        XCTAssertNil(tracker.visibleHerdrPane)
    }

    // MARK: - Coordinator

    private func makeCoordinator(
        presenter: FakeAttentionPresenter,
        events: AsyncStream<AgentEvent>,
        authStates: AsyncStream<AuthState> = AsyncStream { $0.finish() }
    ) -> (AttentionCoordinator, () -> Void) {
        let suiteName = "herdr-attention-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        let seen = AttentionSeenStore(preferences: PreferencesStore(defaults: defaults), agentKey: { "agent-1" })
        let coordinator = AttentionCoordinator(
            eventStream: { events },
            authStates: { authStates },
            presenter: presenter,
            snapshot: { [] },
            seen: seen
        )
        return (coordinator, { defaults.removePersistentDomain(forName: suiteName) })
    }

    func testHerdrAttentionEventPostsThePaneAlert() async {
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let (coordinator, cleanUp) = makeCoordinator(presenter: presenter, events: events)
        defer { cleanUp() }
        coordinator.start()

        continuation.yield(.herdrAttention(attention()))
        continuation.finish()
        await waitUntil { presenter.postedHerdr.count == 1 }

        XCTAssertEqual(presenter.postedHerdr, [attention()])
        XCTAssertTrue(presenter.posted.isEmpty, "a pane alert is not a session alert")
    }

    func testHerdrChangedRemovesTheAlertOfEveryPaneThatNoLongerNeedsTheUser() async {
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let (coordinator, cleanUp) = makeCoordinator(presenter: presenter, events: events)
        defer { cleanUp() }
        coordinator.start()

        continuation.yield(.herdrChanged(snapshot([
            HerdrPane(pane_id: "blocked", agent: "claude", status: "blocked"),
            HerdrPane(pane_id: "done", agent: "claude", status: "done", turn: 2),
            HerdrPane(pane_id: "failed", agent: "claude", status: "failed"),
            HerdrPane(pane_id: "working", agent: "claude", status: "working"),
            HerdrPane(pane_id: "idle", agent: "claude", status: "idle"),
            HerdrPane(pane_id: "shell"),
        ])))
        continuation.finish()
        await waitUntil { presenter.removedHerdrPanes.count == 3 }

        XCTAssertEqual(presenter.removedHerdrPanes, ["working", "idle", "shell"])
        XCTAssertTrue(presenter.removed.isEmpty)
    }

    func testALaterHerdrStateRemovesOnlyTheAlertOfAPaneThatLeftANeedyStatus() async {
        // The whole state arrives on every change of any pane. Removing for
        // every quiet pane each time is one system call per pane per event.
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let (coordinator, cleanUp) = makeCoordinator(presenter: presenter, events: events)
        defer { cleanUp() }
        coordinator.start()
        let first = snapshot([
            HerdrPane(pane_id: "blocked", agent: "claude", status: "blocked"),
            HerdrPane(pane_id: "done", agent: "claude", status: "done", turn: 2),
            HerdrPane(pane_id: "working", agent: "claude", status: "working"),
        ])

        continuation.yield(.herdrChanged(first))
        // Nothing changed for an alert: a title, a folder, a size.
        continuation.yield(.herdrChanged(first))
        // The user answered the prompt at the desktop, and a pane was made.
        continuation.yield(.herdrChanged(snapshot([
            HerdrPane(pane_id: "blocked", agent: "claude", status: "working"),
            HerdrPane(pane_id: "done", agent: "claude", status: "done", turn: 2),
            HerdrPane(pane_id: "working", agent: "claude", status: "idle"),
            HerdrPane(pane_id: "new", agent: "claude", status: "idle"),
        ])))
        // Events are handled in order: this one is behind every removal.
        continuation.yield(.herdrAttention(attention()))
        continuation.finish()
        await waitUntil { presenter.postedHerdr.count == 1 }

        XCTAssertEqual(presenter.postedHerdr.count, 1)
        XCTAssertEqual(presenter.removedHerdrPanes, ["working", "blocked"])
    }

    /// One quiet pane beside one that needs the user. Only the quiet one has
    /// an alert to remove.
    private var quietAndBlocked: HerdrSnapshot {
        snapshot([
            HerdrPane(pane_id: "quiet", agent: "claude", status: "idle"),
            HerdrPane(pane_id: "blocked", agent: "claude", status: "blocked"),
        ])
    }

    func testAfterAResumeTheSameQuietStateRemovesItsAlertAgain() async {
        // A push alert for a pane that went quiet while the app was suspended:
        // the app saw no state in which that pane needed the user, so only a
        // "first state" pass removes it.
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let (coordinator, cleanUp) = makeCoordinator(presenter: presenter, events: events)
        defer { cleanUp() }
        coordinator.start()

        continuation.yield(.herdrChanged(quietAndBlocked))
        continuation.yield(.herdrChanged(quietAndBlocked))
        // Events are handled in order: this one is behind every removal.
        continuation.yield(.herdrAttention(attention()))
        await waitUntil { presenter.postedHerdr.count == 1 }
        XCTAssertEqual(presenter.removedHerdrPanes, ["quiet"])

        coordinator.forgetHerdrState()
        continuation.yield(.herdrChanged(quietAndBlocked))
        continuation.yield(.herdrChanged(quietAndBlocked))
        continuation.yield(.herdrAttention(attention()))
        continuation.finish()
        await waitUntil { presenter.postedHerdr.count == 2 }

        XCTAssertEqual(presenter.removedHerdrPanes, ["quiet", "quiet"], "once more after the resume, and only once")
    }

    func testWhenTheAgentConnectionComesBackTheSameQuietStateRemovesItsAlertAgain() async {
        let presenter = FakeAttentionPresenter()
        let (events, continuation) = AsyncStream.makeStream(of: AgentEvent.self)
        let (authStates, auth) = AsyncStream.makeStream(of: AuthState.self)
        let (coordinator, cleanUp) = makeCoordinator(presenter: presenter, events: events, authStates: authStates)
        defer { cleanUp() }
        coordinator.start()

        continuation.yield(.herdrChanged(quietAndBlocked))
        continuation.yield(.herdrAttention(attention()))
        await waitUntil { presenter.postedHerdr.count == 1 }
        XCTAssertEqual(presenter.removedHerdrPanes, ["quiet"])

        auth.yield(.userOnly)
        auth.yield(.fullyConnected)
        // The coordinator asks for authorization in the turn in which it
        // handles the new connection.
        await waitUntil { presenter.authRequestCount == 1 }
        continuation.yield(.herdrChanged(quietAndBlocked))
        continuation.yield(.herdrAttention(attention()))
        continuation.finish()
        auth.finish()
        await waitUntil { presenter.postedHerdr.count == 2 }

        XCTAssertEqual(presenter.removedHerdrPanes, ["quiet", "quiet"])
    }

    // MARK: - Tap

    private struct SwitchRefused: Error {}

    private func makeDeepLinks(router: Router) -> DeepLinkCoordinator {
        DeepLinkCoordinator(
            router: router,
            authStates: { AsyncStream { $0.finish() } },
            initialAuthState: .fullyConnected,
            connectedAgentId: { "agent-1" },
            switchAgent: { _ in XCTFail("the alert of the connected agent must not switch agents") }
        )
    }

    func testTapOpensTheDashboardOnThatPane() {
        let router = Router()
        let coordinator = makeDeepLinks(router: router)

        coordinator.handleHerdrTap(serverId: "core:default", paneId: "box/t3", agentId: "agent-1")

        XCTAssertEqual(router.path, [.herdr(openServer: "core:default", openPane: "box/t3")])
        XCTAssertNil(coordinator.pendingRoute)
    }

    func testTapWithoutAServerOrAPaneOpensNothing() {
        let router = Router()
        let coordinator = makeDeepLinks(router: router)

        coordinator.handleHerdrTap(serverId: " ", paneId: "box/t3", agentId: nil)
        coordinator.handleHerdrTap(serverId: "core:default", paneId: "", agentId: nil)

        XCTAssertEqual(router.path, [])
        XCTAssertNil(coordinator.pendingRoute)
    }

    func testTapForAnotherAgentWaitsForTheSwitch() {
        // Replayed against the connected agent it would open a pane that
        // agent does not have.
        let router = Router()
        let switched = expectation(description: "agent switch requested")
        let coordinator = DeepLinkCoordinator(
            router: router,
            authStates: { AsyncStream { $0.finish() } },
            initialAuthState: .fullyConnected,
            connectedAgentId: { "agent-1" },
            switchAgent: { agentId in
                XCTAssertEqual(agentId, "agent-2")
                switched.fulfill()
                throw SwitchRefused()
            }
        )

        coordinator.handleHerdrTap(serverId: "core:default", paneId: "box/t3", agentId: "agent-2")

        XCTAssertEqual(router.path, [])
        XCTAssertEqual(coordinator.pendingRoute, .herdr(openServer: "core:default", openPane: "box/t3"))
        wait(for: [switched], timeout: 3)
    }

    // MARK: - Wire

    func testHerdrEventsDemuxFromTheGoldenFixtures() throws {
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/agent/HerdrSnapshot.json") {
            let envelope = try EnvelopeFixtures.envelope(type: "herdr.changed", payload: fixtureCase.wire)
            guard case .herdrChanged? = AgentEnvelopeDemux.demux(envelope) else {
                return XCTFail("case \(fixtureCase.name): expected herdrChanged")
            }
        }
        for fixtureCase in try EnvelopeFixtures.cases(fixture: "dto/agent/HerdrAttention.json") {
            let envelope = try EnvelopeFixtures.envelope(type: "herdr.attention", payload: fixtureCase.wire)
            guard case .herdrAttention(let raised)? = AgentEnvelopeDemux.demux(envelope) else {
                return XCTFail("case \(fixtureCase.name): expected herdrAttention")
            }
            XCTAssertFalse(raised.pane_id.isEmpty, "case \(fixtureCase.name)")
        }
    }

    func testHerdrChangedItCannotDecodeIsNotAnEmptyDashboard() throws {
        // A server without an id: dropping the event keeps the last state,
        // where an empty snapshot would hide running servers.
        let envelope = try EnvelopeFixtures.envelope(
            type: "herdr.changed",
            payload: ["servers": [["name": "no id"]]]
        )

        if case .herdrChanged? = AgentEnvelopeDemux.demux(envelope) {
            XCTFail("a snapshot that does not decode must not reach the dashboard")
        }
    }
}
