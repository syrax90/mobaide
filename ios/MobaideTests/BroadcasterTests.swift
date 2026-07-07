import XCTest
@testable import Mobaide

final class BroadcasterTests: XCTestCase {
    func testLatestReplaySeedsNewSubscriber() async throws {
        let broadcaster = Broadcaster<Int>(replay: .latest)
        broadcaster.emit(41)
        broadcaster.emit(42)
        let collector = StreamCollector(broadcaster.subscribe())
        let value = try await collector.next()
        XCTAssertEqual(value, 42)
    }

    func testNoneReplayLateSubscriberMissesEarlierEvents() async {
        let broadcaster = Broadcaster<Int>(replay: .none)
        broadcaster.emit(1)
        let collector = StreamCollector(broadcaster.subscribe())
        let silent = await collector.expectNoElement()
        XCTAssertTrue(silent)
    }

    func testSubscribeWithoutReplaySkipsCachedValue() async {
        let broadcaster = Broadcaster<Int>(replay: .latest)
        broadcaster.emit(7)
        let collector = StreamCollector(broadcaster.subscribe(replayLatest: false))
        let silent = await collector.expectNoElement()
        XCTAssertTrue(silent)
    }

    func testConflationSkipsEqualEmissions() async throws {
        let broadcaster = Broadcaster<Int>.stateFlow()
        let collector = StreamCollector(broadcaster.subscribe())
        broadcaster.emit(1)
        broadcaster.emit(1)
        broadcaster.emit(2)
        let firstValue = try await collector.next()
        XCTAssertEqual(firstValue, 1)
        let secondValue = try await collector.next()
        XCTAssertEqual(secondValue, 2)
        let silent = await collector.expectNoElement()
        XCTAssertTrue(silent)
    }

    func testClearReplayAffectsOnlyFutureSubscribers() async throws {
        let broadcaster = Broadcaster<String>(replay: .latestClearable)
        broadcaster.emit("stale")
        let early = StreamCollector(broadcaster.subscribe())
        let earlyValue = try await early.next()
        XCTAssertEqual(earlyValue, "stale")

        broadcaster.clearReplay()
        let late = StreamCollector(broadcaster.subscribe())
        let silent = await late.expectNoElement()
        XCTAssertTrue(silent)
    }

    func testTwoSubscribersEachReceiveEveryEventInOrder() async throws {
        let broadcaster = Broadcaster<Int>(replay: .none)
        let first = StreamCollector(broadcaster.subscribe())
        let second = StreamCollector(broadcaster.subscribe())
        for value in 0..<5 {
            broadcaster.emit(value)
        }
        for value in 0..<5 {
            let firstReceived = try await first.next()
            XCTAssertEqual(firstReceived, value)
            let secondReceived = try await second.next()
            XCTAssertEqual(secondReceived, value)
        }
    }
}
