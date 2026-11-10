import UIKit
import XCTest
@testable import Mobaide

/// A picked image counts as preparing from the start of its load (an iCloud
/// photo can take seconds), so Send stays off until it is attached; a load
/// that fails shows a toast and frees Send.
@MainActor
final class ChatSessionModelImagesTests: XCTestCase {

    /// An image load the test finishes by hand.
    @MainActor
    private final class PendingLoad {
        private(set) var started = false
        private var continuation: CheckedContinuation<Data?, Never>?

        func load() async -> Data? {
            await withCheckedContinuation { continuation in
                self.continuation = continuation
                started = true
            }
        }

        func finish(_ data: Data?) {
            continuation?.resume(returning: data)
            continuation = nil
        }
    }

    private func png() throws -> Data {
        let image = UIGraphicsImageRenderer(size: CGSize(width: 8, height: 8)).image { context in
            UIColor.red.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 8, height: 8))
        }
        return try XCTUnwrap(image.pngData())
    }

    /// A model on a live socket whose snapshot says the agent takes images.
    private func liveModel() async throws -> ChatSessionModel {
        let factory = FakeWebSocketTransportFactory()
        let harness = SessionHarness(userLoggedIn: true, agentConnected: true)
        let tracker = ForegroundSessionTracker()
        let model = ChatSessionModel(
            sessionId: "chat-1",
            webSocketFactory: WebSocketCoreFactory.live(transportFactory: factory, sleeper: RecordingSleeper()),
            sessionManager: harness.sessionManager,
            tokenStore: harness.tokenStore,
            foregroundSessionTracker: tracker,
            reviewPrompt: TerminalTestSupport.reviewPrompt(
                preferencesStore: TerminalTestSupport.isolatedPreferencesStore(),
                foregroundSessionTracker: tracker
            )
        )
        model.markVisible()
        let connection = try await factory.connection(1)
        connection.emit(.opened)
        let snapshot = #"{"t":"snapshot","epoch":"e1","seq":1,"truncated":false,"state":{"status":"ready","prompt_caps":{"image":true}},"items":[]}"#
        connection.emit(.binary(Data([ChatFrameType.agent]) + Data(snapshot.utf8)))
        await waitUntil { model.canAct && model.canAttachImages }
        XCTAssertTrue(model.canAct && model.canAttachImages, "the model never went live with image support")
        return model
    }

    func testSendWaitsForAnImageWhoseLoadIsStillRunning() async throws {
        let model = try await liveModel()
        defer { model.disconnect() }
        model.composerText = "what is on this?"
        XCTAssertTrue(model.canSend)
        let pending = PendingLoad()

        model.addImage { await pending.load() }
        await waitUntil { pending.started }

        XCTAssertTrue(pending.started)
        XCTAssertEqual(model.preparingImages, 1)
        XCTAssertFalse(model.canSend)

        pending.finish(try png())
        await waitUntil { model.preparingImages == 0 }

        XCTAssertEqual(model.preparingImages, 0)
        XCTAssertEqual(model.attachments.count, 1)
        XCTAssertTrue(model.canSend)
    }

    func testAFailedLoadShowsAToastAndFreesSend() async throws {
        let model = try await liveModel()
        defer { model.disconnect() }
        model.composerText = "what is on this?"
        let pending = PendingLoad()

        model.addImage { await pending.load() }
        await waitUntil { pending.started }
        XCTAssertFalse(model.canSend)

        pending.finish(nil)
        await waitUntil { model.preparingImages == 0 }

        XCTAssertEqual(model.preparingImages, 0)
        XCTAssertTrue(model.attachments.isEmpty)
        XCTAssertEqual(model.message?.text, "This image could not be attached")
        XCTAssertEqual(model.message?.tone, .danger)
        XCTAssertTrue(model.canSend)
    }
}
