import XCTest
@testable import Mobaide

@MainActor
final class DevicesModelTests: XCTestCase {
    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    private func makeModel() -> DevicesModel {
        let harness = AgentHarness()
        return DevicesModel(
            deviceRepository: harness.deviceRepository,
            deviceIdentityStore: harness.deviceIdentityStore
        )
    }

    func testCurrentDeviceBadge() {
        let model = makeModel()
        let current = DeviceResponse(device_id: model.currentDeviceId, name: "Me", created_at: "t", last_seen_at: nil)
        let other = DeviceResponse(device_id: "other-id", name: "X", created_at: "t", last_seen_at: nil)
        XCTAssertTrue(model.isCurrent(current))
        XCTAssertFalse(model.isCurrent(other))
    }

    func testLoadDevices() async {
        let model = makeModel()
        StubURLProtocol.enqueue(status: 200, body: #"[{"device_id":"d1","created_at":"t"},{"device_id":"d2","created_at":"t"}]"#)
        await model.refresh()
        XCTAssertEqual(model.devices.map(\.device_id), ["d1", "d2"])
    }

    func testRevokeRemovesRowOptimisticallyThenPersists() async {
        let model = makeModel()
        StubURLProtocol.enqueue(status: 200, body: #"[{"device_id":"d1","created_at":"t"},{"device_id":"d2","created_at":"t"}]"#)
        await model.refresh()

        StubURLProtocol.enqueue(status: 204, body: "")
        model.confirmRevoke(model.devices[0])
        XCTAssertEqual(model.devices.map(\.device_id), ["d2"])  // removed immediately

        await waitUntil { StubURLProtocol.requests.count >= 2 }
        XCTAssertEqual(model.devices.map(\.device_id), ["d2"])
        XCTAssertNil(model.message)
    }

    func testRevokeRollsBackOnFailure() async {
        let model = makeModel()
        StubURLProtocol.enqueue(status: 200, body: #"[{"device_id":"d1","created_at":"t"}]"#)
        await model.refresh()

        StubURLProtocol.enqueue(status: 500, body: "boom")
        model.confirmRevoke(model.devices[0])
        XCTAssertTrue(model.devices.isEmpty)  // optimistic removal

        await waitUntil { model.message != nil }
        XCTAssertEqual(model.devices.map(\.device_id), ["d1"])  // rolled back
        XCTAssertNotNil(model.message)
    }
}
