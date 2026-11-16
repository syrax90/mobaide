import XCTest
@testable import Mobaide

/// `TerminalSessionModel` as the view of a Herdr pane. In watch mode the agent
/// dictates the grid (the `pane` block of the state frame) and a size from
/// this client would resize the pane under a user at the desktop, so no `r`
/// frame may leave while that grid stands. Taking control hands the size back.
@MainActor
final class TerminalSessionModelPaneGridTests: XCTestCase {
    private static let watch = #"{"status":"running","pane":{"mode":"watch","cols":84,"rows":47}}"#
    private static let control = #"{"status":"running","pane":{"mode":"control"}}"#

    private func connectedModel() async throws -> (TerminalSessionModel, FakeWebSocketConnection) {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(sessionId: "herdr-0123", transportFactory: factory)
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        return (model, connection)
    }

    private func emitState(_ json: String, on connection: FakeWebSocketConnection) {
        connection.emit(.binary(Data([0x73]) + Data(json.utf8)))
    }

    private func resizes(_ connection: FakeWebSocketConnection) -> [Data] {
        connection.sentBinaries.filter { $0.first == 0x72 }
    }

    func testStateFrameExposesThePaneBlock() async throws {
        let (model, connection) = try await connectedModel()

        emitState(Self.watch, on: connection)
        await waitUntil { model.paneState != nil }
        XCTAssertEqual(model.paneState, TerminalPaneState(mode: "watch", cols: 84, rows: 47))

        emitState(Self.control, on: connection)
        await waitUntil { model.paneState?.isControl == true }
        XCTAssertEqual(model.paneState, TerminalPaneState(mode: "control"))

        emitState(#"{"status":"running","pid":1234}"#, on: connection)
        await waitUntil { model.paneState == nil }
        XCTAssertNil(model.paneState, "a normal session has no pane block")
    }

    func testWatchGridSendsNoResizeOnLayoutOrOnReconnect() async throws {
        let (model, connection) = try await connectedModel()
        emitState(Self.watch, on: connection)
        await waitUntil { model.paneState?.grid != nil }

        model.resize(rows: 47, cols: 84) // the layout applies the dictated grid
        connection.emit(.opened) // reconnect → the cached size would be re-sent
        try await Task.sleep(for: .milliseconds(200)) // past the resize debounce

        XCTAssertEqual(resizes(connection), [], "a watched pane keeps its own size")
    }

    func testTakingControlHandsTheSizeBackToThisClient() async throws {
        let (model, connection) = try await connectedModel()
        emitState(Self.watch, on: connection)
        await waitUntil { model.paneState?.grid != nil }
        model.resize(rows: 47, cols: 84)
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(resizes(connection), [])

        emitState(controlled(by: PaneClient.id), on: connection)
        await waitUntil { model.paneState?.isControl == true }
        model.resize(rows: 40, cols: 120)
        await waitUntil { self.resizes(connection).last == Data([0x72, 0x00, 0x28, 0x00, 0x78]) }

        XCTAssertEqual(
            resizes(connection).last,
            Data([0x72, 0x00, 0x28, 0x00, 0x78]), // 40 rows, 120 cols
            "in control the pane takes this client's size"
        )
    }

    // MARK: - No size unless the current socket names this client the controller

    /// A pane in control mode at the controller's grid, as the agent sends it
    /// to every viewer.
    private func controlled(by clientId: String) -> String {
        #"{"status":"running","pane":{"mode":"control","controller":"\#(clientId)","cols":84,"rows":47}}"#
    }

    /// The model the pane screen makes: it is a pane view before any frame.
    private func connectedPaneView() async throws -> (TerminalSessionModel, FakeWebSocketConnection) {
        let factory = FakeWebSocketTransportFactory()
        let model = TerminalTestSupport.model(sessionId: "herdr-0123", transportFactory: factory)
        model.isHerdrPane = true
        model.start()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        return (model, connection)
    }

    /// The `r` frame of the grid the view has now.
    private func ownGridFrame(_ model: TerminalSessionModel) -> Data {
        let terminal = model.terminalView.getTerminal()
        return TerminalOutboundFrame.resize(
            rows: UInt16(clamping: max(1, terminal.rows)), cols: UInt16(clamping: max(1, terminal.cols))
        ).encoded
    }

    func testAPaneViewSendsNoSizeUntilAStateFrameNamesThisClientTheController() async throws {
        let (model, connection) = try await connectedPaneView()

        // Before any state frame: a session sends this grid, a pane view must not.
        model.resize(rows: 30, cols: 90)
        try await Task.sleep(for: .milliseconds(200)) // past the resize debounce
        XCTAssertEqual(resizes(connection), [], "nothing is known yet about who owns the size")

        emitState(Self.watch, on: connection)
        await waitUntil { model.paneState?.grid != nil }
        model.resize(rows: 47, cols: 84)
        emitState(controlled(by: "another-device"), on: connection)
        await waitUntil { model.paneState?.isControlledElsewhere == true }
        model.resize(rows: 31, cols: 91)
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(resizes(connection), [], "a watcher, and a view of a pane another device controls")

        emitState(controlled(by: PaneClient.id), on: connection)
        await waitUntil { !self.resizes(connection).isEmpty }

        XCTAssertEqual(resizes(connection), [ownGridFrame(model)], "the controller's grid goes out at once")
    }

    func testAfterAReconnectNoSizeLeavesUntilTheNewSocketNamesThisClient() async throws {
        // Control ends when the last viewer leaves, so a view that comes back
        // is a watcher until the agent says otherwise. A size sent on what the
        // old socket said would resize a pane that another device controls now.
        let (model, connection) = try await connectedPaneView()
        emitState(controlled(by: PaneClient.id), on: connection)
        await waitUntil { model.paneState?.isControl == true }
        model.resize(rows: 40, cols: 120)
        let own = Data([0x72, 0x00, 0x28, 0x00, 0x78]) // 40 rows, 120 cols
        await waitUntil { self.resizes(connection).last == own }
        let before = resizes(connection)
        XCTAssertEqual(before.last, own)

        connection.emit(.opened) // the socket opens again
        try await Task.sleep(for: .milliseconds(50))
        model.resize(rows: 41, cols: 121)
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(resizes(connection), before, "the state of the old socket gives no right to a size")

        emitState(controlled(by: PaneClient.id), on: connection)
        await waitUntil { self.resizes(connection).count == before.count + 1 }

        XCTAssertEqual(resizes(connection), before + [ownGridFrame(model)])
    }
}
