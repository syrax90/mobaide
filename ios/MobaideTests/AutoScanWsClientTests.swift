import XCTest
@testable import Mobaide

final class AutoScanWsClientTests: XCTestCase {
    private func makeClient(
        factory: FakeWebSocketTransportFactory,
        sleeper: RecordingSleeper = RecordingSleeper()
    ) -> AutoScanWsClient {
        AutoScanWsClient(
            webSocketFactory: .live(transportFactory: factory, sleeper: sleeper),
            urlProvider: { "ws://host/ws/autoscan?token=t" },
            isAuthenticated: { true }
        )
    }

    private func json(_ object: [String: Any]) -> String {
        String(data: try! JSONSerialization.data(withJSONObject: object), encoding: .utf8)!
    }

    private func eventFrame(
        project: String,
        seq: Int64,
        type: String = "log",
        payload: [String: Any] = ["line": "x", "level": "info", "ansi": false]
    ) -> String {
        json(["type": "event", "project_name": project,
              "event": ["seq": seq, "ts": "2026-07-08T00:00:00Z", "type": type, "payload": payload]])
    }

    private func snapshotFrame(scans: [[String: Any]] = [], events: [String: [[String: Any]]] = [:]) -> String {
        json(["type": "snapshot", "data": ["scans": scans, "events": events]])
    }

    private func parse(_ text: String) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any]) ?? [:]
    }

    private func resumeMap(from sent: String) -> [String: Any] {
        parse(sent)["resume_from_seq"] as? [String: Any] ?? [:]
    }

    // MARK: subscribe frame

    func testSubscribeFrameHasEmptyArrayAndObjectByDefault() async throws {
        let factory = FakeWebSocketTransportFactory()
        let collector = StreamCollector(makeClient(factory: factory).connect(subscribe: [], resumeFromSeq: [:]))
        let conn = try await factory.connection(1)
        conn.emit(.opened)
        let firstFrame = try await collector.next()
        XCTAssertEqual(firstFrame, .connected)

        let sent = parse(try XCTUnwrap(conn.sentTexts.first))
        XCTAssertEqual(sent["subscribe"] as? [String], [])
        XCTAssertTrue((sent["resume_from_seq"] as? [String: Any])?.isEmpty ?? false)
    }

    func testSubscribeFrameKeepsZeroValuedResumeEntries() async throws {
        let factory = FakeWebSocketTransportFactory()
        let stream = makeClient(factory: factory)
            .connect(subscribe: ["proj-a", "proj-b"], resumeFromSeq: ["proj-a": 42, "proj-b": 0])
        let collector = StreamCollector(stream)
        let conn = try await factory.connection(1)
        conn.emit(.opened)
        _ = try await collector.next()

        let sent = parse(try XCTUnwrap(conn.sentTexts.first))
        XCTAssertEqual(Set(sent["subscribe"] as? [String] ?? []), ["proj-a", "proj-b"])
        let resume = try XCTUnwrap(sent["resume_from_seq"] as? [String: Any])
        XCTAssertEqual((resume["proj-a"] as? NSNumber)?.int64Value, 42)
        XCTAssertEqual((resume["proj-b"] as? NSNumber)?.int64Value, 0)
    }

    // MARK: decoding

    func testDecodesSnapshotCaughtUpAndEventFrames() async throws {
        let factory = FakeWebSocketTransportFactory()
        let collector = StreamCollector(makeClient(factory: factory).connect(subscribe: ["proj-a"]))
        let conn = try await factory.connection(1)
        conn.emit(.opened)
        _ = try await collector.next()

        let scan: [String: Any] = [
            "project_name": "proj-a", "status": "scanning",
            "started_at": 1.5, "last_activity_at": 1.5, "cycle_number": 5, "current_seq": 0,
        ]
        let bufferedEvent: [String: Any] = ["seq": 1, "ts": "T", "type": "phase", "payload": ["phase": "scan_started", "cycle_number": 5]]
        conn.emit(.text(snapshotFrame(scans: [scan], events: ["proj-a": [bufferedEvent]])))
        let snapshot = try await collector.next()
        guard case .snapshot(let scans, let events) = snapshot else { return XCTFail("expected snapshot, got \(snapshot)") }
        XCTAssertEqual(scans.first?.project_name, "proj-a")
        XCTAssertEqual(events["proj-a"]?.count, 1)

        conn.emit(.text(json(["type": "caught_up"])))
        let caughtUp = try await collector.next()
        XCTAssertEqual(caughtUp, .caughtUp)

        conn.emit(.text(eventFrame(project: "proj-a", seq: 2, type: "worktree", payload: ["identifier": "ISS-2", "path": "/w/ISS-2"])))
        let event = try await collector.next()
        guard case .event(let parsed) = event else { return XCTFail("expected event, got \(event)") }
        XCTAssertEqual(parsed.seq, 2)
        XCTAssertEqual(parsed.payload, .worktree(identifier: "ISS-2", path: "/w/ISS-2"))
    }

    func testEventForDifferentSeqSurvivesAfterSnapshot() async throws {
        let factory = FakeWebSocketTransportFactory()
        let collector = StreamCollector(makeClient(factory: factory).connect(subscribe: ["proj-a"]))
        let conn = try await factory.connection(1)
        conn.emit(.opened)
        _ = try await collector.next()

        conn.emit(.text(eventFrame(project: "proj-a", seq: 1)))
        guard case .event(let first) = try await collector.next() else { return XCTFail() }
        XCTAssertEqual(first.seq, 1)
        conn.emit(.text(eventFrame(project: "proj-a", seq: 2)))
        guard case .event(let second) = try await collector.next() else { return XCTFail() }
        XCTAssertEqual(second.seq, 2)
    }

    // MARK: the resume_from_seq fix

    func testReconnectRebuildsResumeFromLiveCursor() async throws {
        let factory = FakeWebSocketTransportFactory()
        let stream = makeClient(factory: factory).connect(subscribe: ["proj-a"], resumeFromSeq: [:])
        let collector = StreamCollector(stream)

        let conn1 = try await factory.connection(1)
        conn1.emit(.opened)
        _ = try await collector.next()
        XCTAssertTrue(resumeMap(from: try XCTUnwrap(conn1.sentTexts.first)).isEmpty, "initial resume is empty")

        conn1.emit(.text(eventFrame(project: "proj-a", seq: 7)))
        _ = try await collector.next(where: { if case .event = $0 { return true } else { return false } })

        conn1.emit(.closed(code: 1006, reason: ""))
        _ = try await collector.next(where: { $0 == .disconnected })

        let conn2 = try await factory.connection(2)
        conn2.emit(.opened)
        _ = try await collector.next(where: { $0 == .connected })

        let resume = resumeMap(from: try XCTUnwrap(conn2.sentTexts.first))
        XCTAssertEqual((resume["proj-a"] as? NSNumber)?.int64Value, 7,
                       "reconnect must resume from the LIVE cursor, not the value captured at connect")
    }

    func testSnapshotEventsAdvanceWireCursorForReconnect() async throws {
        let factory = FakeWebSocketTransportFactory()
        let stream = makeClient(factory: factory).connect(subscribe: ["proj-a"], resumeFromSeq: [:])
        let collector = StreamCollector(stream)

        let conn1 = try await factory.connection(1)
        conn1.emit(.opened)
        _ = try await collector.next()

        let buffered: [String: Any] = ["seq": 3, "ts": "T", "type": "log", "payload": ["line": "x"]]
        conn1.emit(.text(snapshotFrame(events: ["proj-a": [buffered]])))
        _ = try await collector.next(where: { if case .snapshot = $0 { return true } else { return false } })

        conn1.emit(.closed(code: 1006, reason: ""))
        _ = try await collector.next(where: { $0 == .disconnected })

        let conn2 = try await factory.connection(2)
        conn2.emit(.opened)
        _ = try await collector.next(where: { $0 == .connected })

        let resume = resumeMap(from: try XCTUnwrap(conn2.sentTexts.first))
        XCTAssertEqual((resume["proj-a"] as? NSNumber)?.int64Value, 3,
                       "snapshot-buffered events must advance the wire cursor")
    }

    func testDuplicateSeqDeliveredOnce() async throws {
        let factory = FakeWebSocketTransportFactory()
        let collector = StreamCollector(makeClient(factory: factory).connect(subscribe: ["proj-a"]))
        let conn = try await factory.connection(1)
        conn.emit(.opened)
        _ = try await collector.next()

        conn.emit(.text(eventFrame(project: "proj-a", seq: 5)))
        guard case .event(let first) = try await collector.next() else { return XCTFail() }
        XCTAssertEqual(first.seq, 5)

        conn.emit(.text(eventFrame(project: "proj-a", seq: 5)))
        let quiet = await collector.expectNoElement()
        XCTAssertTrue(quiet, "a duplicate seq must not be delivered a second time")
    }

    // MARK: resilience & lifecycle

    func testMalformedJsonIsDroppedAndStreamSurvives() async throws {
        let factory = FakeWebSocketTransportFactory()
        let collector = StreamCollector(makeClient(factory: factory).connect(subscribe: ["proj-a"]))
        let conn = try await factory.connection(1)
        conn.emit(.opened)
        _ = try await collector.next()

        conn.emit(.text("{ not json"))
        let noElement = await collector.expectNoElement()
        XCTAssertTrue(noElement)

        conn.emit(.text(json(["type": "caught_up"])))
        let recovered = try await collector.next()
        XCTAssertEqual(recovered, .caughtUp)
    }

    func testCancellingStreamDisconnectsSocket() async throws {
        let factory = FakeWebSocketTransportFactory()
        let stream = makeClient(factory: factory).connect(subscribe: ["proj-a"])
        let consume = Task { for await _ in stream {} }
        let conn = try await factory.connection(1)
        conn.emit(.opened)
        try await Task.sleep(for: .milliseconds(20))
        consume.cancel()

        var tornDown = false
        for _ in 0..<200 where !tornDown {
            if conn.closeCalls.contains(where: { $0.code == 1000 }) || conn.wasCancelled {
                tornDown = true
            } else {
                try await Task.sleep(for: .milliseconds(5))
            }
        }
        XCTAssertTrue(tornDown, "cancelling the frame stream must disconnect the socket")
    }

    // MARK: golden frame fixtures

    private struct FrameFixtureFile: Decodable { let cases: [FrameCase] }
    private struct FrameCase: Decodable { let name: String; let wire: JSONValue; let expect: Expect }
    private struct Expect: Decodable {
        let kind: String
        let project_name: String?
        let seq: Int64?
        let payload_type: String?
        let scan_count: Int?
        let event_count: Int?
    }

    func testFrameFixtures() throws {
        let file = try FixtureLoader.load(FrameFixtureFile.self, path: "frames/autoscan_frames.json")
        XCTAssertGreaterThanOrEqual(file.cases.count, 10)
        for testCase in file.cases {
            let data = try JSONEncoder().encode(testCase.wire)
            let wireText = String(decoding: data, as: UTF8.self)
            let frame = AutoScanWsClient.decodeFrame(wireText)
            switch testCase.expect.kind {
            case "null":
                XCTAssertNil(frame, "case \(testCase.name)")
            case "caught_up":
                XCTAssertEqual(frame, .caughtUp, "case \(testCase.name)")
            case "snapshot":
                guard case .snapshot(let scans, let events)? = frame else { return XCTFail("case \(testCase.name)") }
                XCTAssertEqual(scans.count, testCase.expect.scan_count, "case \(testCase.name)")
                XCTAssertEqual(events.values.reduce(0) { $0 + $1.count }, testCase.expect.event_count, "case \(testCase.name)")
            case "event":
                guard case .event(let event)? = frame else { return XCTFail("case \(testCase.name)") }
                XCTAssertEqual(event.projectName, testCase.expect.project_name, "case \(testCase.name)")
                XCTAssertEqual(event.seq, testCase.expect.seq, "case \(testCase.name)")
                XCTAssertEqual(Self.payloadType(event.payload), testCase.expect.payload_type, "case \(testCase.name)")
            default:
                XCTFail("unhandled expect kind \(testCase.expect.kind) in \(testCase.name)")
            }
        }
    }

    private static func payloadType(_ payload: ScanProgressPayload) -> String {
        switch payload {
        case .phase: return "phase"
        case .issue: return "issue"
        case .worktree: return "worktree"
        case .session: return "session"
        case .log: return "log"
        case .unknown: return "unknown"
        }
    }
}
