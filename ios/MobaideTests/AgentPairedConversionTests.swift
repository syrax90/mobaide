import XCTest
@testable import Mobaide

final class AgentPairedConversionTests: XCTestCase {
    private struct LoggedEvent: Equatable {
        let name: String
        let parameters: [String: String]
    }

    private final class RecordingSink: AnalyticsSink, @unchecked Sendable {
        private let lock = NSLock()
        private var enabledStorage = true
        private var eventsStorage: [LoggedEvent] = []

        var isEnabled: Bool { lock.withLock { enabledStorage } }
        var events: [LoggedEvent] { lock.withLock { eventsStorage } }

        func setEnabled(_ enabled: Bool) { lock.withLock { enabledStorage = enabled } }

        func logEvent(_ name: String, parameters: [String: String]) {
            lock.withLock { eventsStorage.append(LoggedEvent(name: name, parameters: parameters)) }
        }
    }

    private final class MeStub: @unchecked Sendable {
        private let lock = NSLock()
        private var resultStorage: Result<MeResponse, Error>
        private var callsStorage = 0

        init(_ result: Result<MeResponse, Error>) { resultStorage = result }

        var calls: Int { lock.withLock { callsStorage } }

        func fetch(_ token: String) async throws -> MeResponse {
            let result = lock.withLock { () -> Result<MeResponse, Error> in
                callsStorage += 1
                return resultStorage
            }
            return try result.get()
        }
    }

    private struct Boom: Error {}

    private static let now = Date(timeIntervalSince1970: 1_800_000_000)

    private var stateDirectory: URL!
    private var state: ConversionStateStore!
    private var identity: UserIdentityStore!

    override func setUp() {
        super.setUp()
        stateDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("agent-paired-\(UUID().uuidString)", isDirectory: true)
        state = ConversionStateStore(directory: stateDirectory)
        identity = UserIdentityStore(keychain: InMemoryKeychainStore())
        identity.saveUserSession(accessToken: "tok", email: "e@x.com", userId: "u1", role: "viewer")
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: stateDirectory)
        super.tearDown()
    }

    private func me(firstPairedAt: String?, userId: String = "u1") -> MeResponse {
        MeResponse(
            user_id: userId,
            email: "e@x.com",
            role: "viewer",
            created_at: "2026-09-01T10:00:00+00:00",
            first_paired_at: firstPairedAt
        )
    }

    private static func stamp(_ date: Date, fractional: Bool = true) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = fractional
            ? [.withInternetDateTime, .withFractionalSeconds]
            : [.withInternetDateTime]
        return formatter.string(from: date)
    }

    private func makeConversion(
        me result: Result<MeResponse, Error>,
        sinkEnabled: Bool = true,
        now: Date = AgentPairedConversionTests.now
    ) -> (AgentPairedConversion, RecordingSink, MeStub) {
        let sink = RecordingSink()
        sink.setEnabled(sinkEnabled)
        let stub = MeStub(result)
        let conversion = AgentPairedConversion(
            sink: sink,
            fetchMe: { try await stub.fetch($0) },
            userIdentityStore: identity,
            state: state,
            now: { now }
        )
        return (conversion, sink, stub)
    }

    // MARK: - Live event

    func testLiveEventLogsOnceAndPersistsTheOutcome() async {
        let (conversion, sink, _) = makeConversion(me: .success(me(firstPairedAt: nil)))

        await conversion.firstAgentPaired(agentId: "a1")
        await conversion.firstAgentPaired(agentId: "a1")

        XCTAssertEqual(sink.events, [LoggedEvent(name: "agent_paired", parameters: ["source": "live"])])
        XCTAssertEqual(state.getOutcome(userId: "u1"), "logged")
    }

    // MARK: - Reconcile

    func testReconcileLogsAPairingAfterArming() async {
        let paired = Self.now.addingTimeInterval(600)
        let (conversion, sink, _) = makeConversion(me: .success(me(firstPairedAt: Self.stamp(paired))))

        await conversion.armIfNeeded()
        await conversion.reconcileAfterConnect()

        XCTAssertEqual(sink.events, [LoggedEvent(name: "agent_paired", parameters: ["source": "reconcile"])])
        XCTAssertEqual(state.getOutcome(userId: "u1"), "logged")
    }

    func testReconcileAcceptsAStampWithoutFractionalSeconds() async {
        let paired = Self.now.addingTimeInterval(600)
        let (conversion, sink, _) = makeConversion(
            me: .success(me(firstPairedAt: Self.stamp(paired, fractional: false)))
        )

        await conversion.reconcileAfterConnect()

        XCTAssertEqual(sink.events.map(\.name), ["agent_paired"])
    }

    func testReconcileSelfArmsWhenLaunchArmingDidNotRun() async {
        let (conversion, _, _) = makeConversion(me: .success(me(firstPairedAt: nil)))

        await conversion.reconcileAfterConnect()

        XCTAssertEqual(state.getArmedAt(), Self.now)
        XCTAssertNil(state.getOutcome(userId: "u1"))
    }

    func testUnpairedAccountPersistsNothingAndRetriesNextConnect() async {
        let (conversion, sink, stub) = makeConversion(me: .success(me(firstPairedAt: nil)))

        await conversion.reconcileAfterConnect()
        await conversion.reconcileAfterConnect()

        XCTAssertEqual(sink.events, [])
        XCTAssertEqual(stub.calls, 2)
        XCTAssertNil(state.getOutcome(userId: "u1"))
    }

    func testPairingOlderThanTheInstallIsSkippedForGood() async {
        let paired = Self.now.addingTimeInterval(-7200)
        let (conversion, sink, stub) = makeConversion(me: .success(me(firstPairedAt: Self.stamp(paired))))

        await conversion.armIfNeeded()
        await conversion.reconcileAfterConnect()
        await conversion.reconcileAfterConnect()

        XCTAssertEqual(sink.events, [])
        XCTAssertEqual(state.getOutcome(userId: "u1"), "skipped_predates_install")
        XCTAssertEqual(stub.calls, 1, "a stored outcome short-circuits before the fetch")
    }

    func testPairingInsideTheSkewToleranceStillLogs() async {
        let paired = Self.now.addingTimeInterval(-1800)
        let (conversion, sink, _) = makeConversion(me: .success(me(firstPairedAt: Self.stamp(paired))))

        await conversion.armIfNeeded()
        await conversion.reconcileAfterConnect()

        XCTAssertEqual(sink.events.map(\.name), ["agent_paired"])
    }

    func testFetchFailurePersistsNothing() async {
        let (conversion, sink, _) = makeConversion(me: .failure(Boom()))

        await conversion.reconcileAfterConnect()

        XCTAssertEqual(sink.events, [])
        XCTAssertNil(state.getOutcome(userId: "u1"))
    }

    func testProfileOfAnotherUserIsIgnored() async {
        let paired = Self.now.addingTimeInterval(600)
        let (conversion, sink, _) = makeConversion(
            me: .success(me(firstPairedAt: Self.stamp(paired), userId: "u2"))
        )

        await conversion.reconcileAfterConnect()

        XCTAssertEqual(sink.events, [])
        XCTAssertNil(state.getOutcome(userId: "u1"))
        XCTAssertNil(state.getOutcome(userId: "u2"))
    }

    func testLiveEventAndReconcileTogetherLogExactlyOnce() async {
        let paired = Self.now.addingTimeInterval(600)
        let (conversion, sink, _) = makeConversion(me: .success(me(firstPairedAt: Self.stamp(paired))))

        async let live: Void = conversion.firstAgentPaired(agentId: "a1")
        async let reconcile: Void = conversion.reconcileAfterConnect()
        _ = await (live, reconcile)

        XCTAssertEqual(sink.events.count, 1)
        XCTAssertEqual(state.getOutcome(userId: "u1"), "logged")
    }

    func testOutcomesAreKeyedPerAccount() async {
        let (conversion, sink, _) = makeConversion(me: .success(me(firstPairedAt: nil)))
        await conversion.firstAgentPaired(agentId: "a1")
        identity.saveUserSession(accessToken: "tok2", email: "o@x.com", userId: "u2", role: "viewer")

        await conversion.firstAgentPaired(agentId: "a2")

        XCTAssertEqual(sink.events.count, 2)
        XCTAssertEqual(state.getOutcome(userId: "u1"), "logged")
        XCTAssertEqual(state.getOutcome(userId: "u2"), "logged")
    }
}
