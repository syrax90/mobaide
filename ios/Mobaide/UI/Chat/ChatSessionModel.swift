import Foundation
import Observation
import UIKit

/// An image picked for the next prompt: already downscaled and encoded.
struct ChatComposerImage: Identifiable {
    let id = UUID()
    let payload: ChatImagePayload
    let thumbnail: UIImage
}

/// Owns one chat session's socket and its client copy of the timeline. Cached
/// by `ChatSessionStore`, so switching sessions re-renders at once and a
/// reopened socket syncs only the missed events.
///
/// The socket stays open while the screen is on screen (or a session-scoped
/// child screen holds it) and the app is active; otherwise it detaches after
/// `detachGrace`. Commands carry `<clientId>-<n>` ids; results for other ids
/// come from other clients and are ignored.
@MainActor
@Observable
final class ChatSessionModel {
    enum Connection: Equatable {
        case connecting
        case live
        case detached
        case closed
    }

    static let maxImages = 4
    static let maxImageBase64 = 1_500_000
    /// All images of one prompt together; the frame must stay under the
    /// relay's 4 MiB limit.
    static let maxImagesBase64Total = 3_500_000
    static let maxPromptLength = 100_000
    static let detachGrace: Duration = .seconds(20)
    /// A result comes at once or within the agent's 20 s config timeout; a
    /// large frame gets extra time per MB to upload.
    static let commandTimeout: Duration = .seconds(30)
    static let commandTimeoutPerMegabyte: Duration = .seconds(20)
    static let connectionDroppedNotice = "The connection dropped. Check that the message was sent."

    let sessionId: String

    private(set) var items: [ChatItem] = []
    private(set) var state = ChatState() {
        didSet { syncReviewMeter() }
    }
    private(set) var hasSnapshot = false
    private(set) var truncated = false
    /// Bumped on every timeline content change; drives auto-follow.
    private(set) var itemsVersion = 0
    /// Bumped when the user sends: the timeline jumps to the bottom and follows.
    private(set) var followRequests = 0
    private(set) var connection: Connection = .connecting {
        didSet { syncReviewMeter() }
    }
    /// The `s` frame said `stopped`: the session is gone for good.
    private(set) var sessionClosed = false {
        didSet { syncReviewMeter() }
    }

    var composerText = ""
    /// The composer's selection in character offsets (iOS 18+); nil inserts at the end.
    var composerSelection: Range<Int>?
    private(set) var composerCursorRequest = 0
    private(set) var attachments: [ChatComposerImage] = []
    private(set) var preparingImages = 0
    var message: ToastMessage?

    /// Request id → the option id (or form action) being sent.
    private(set) var answering: [String: String] = [:]
    private(set) var formErrors: [String: String] = [:]
    private(set) var configOverrides: [String: ChatConfigValue] = [:]
    private(set) var dequeuing: Set<String> = []
    private(set) var restarting = false
    /// Rows the user expanded; kept here so a row scrolled out of the lazy
    /// list comes back the way it was left.
    private(set) var expandedItems: Set<String> = []

    @ObservationIgnored private var timeline = ChatTimeline()
    @ObservationIgnored private let clientId = String(UUID().uuidString.prefix(8)).lowercased()
    @ObservationIgnored private var commandCounter = 0
    @ObservationIgnored private var pending: [String: PendingCommand] = [:]
    @ObservationIgnored private var deadlines: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var lastSync: (request: ChatTimeline.SyncRequest, at: Date)?

    @ObservationIgnored private let webSocketFactory: WebSocketCoreFactory
    @ObservationIgnored private let sessionManager: SessionManager
    @ObservationIgnored private let tokenStore: TokenStore
    @ObservationIgnored private let foregroundSessionTracker: ForegroundSessionTracker
    @ObservationIgnored private let reviewPrompt: ReviewPromptCoordinator
    @ObservationIgnored private var core: WebSocketCore?
    @ObservationIgnored private var eventTask: Task<Void, Never>?
    @ObservationIgnored private var sendTask: Task<Void, Never>?
    @ObservationIgnored private var detachTask: Task<Void, Never>?
    @ObservationIgnored private let outbound: AsyncStream<Outbound>
    @ObservationIgnored private let outboundContinuation: AsyncStream<Outbound>.Continuation
    @ObservationIgnored private var screenVisible = false
    @ObservationIgnored private var appActive = true
    /// Healthy use of this visit, for the review prompt.
    @ObservationIgnored private var reviewMeter = HealthyTimeMeter()
    /// The user stopped this session, so leaving it is a clean end rather than a failure.
    @ObservationIgnored private var endedByUser = false
    @ObservationIgnored private var isTornDown = false
    /// Once the session is known to exist it is never treated as missing: a
    /// session that ends while the user reads it keeps its timeline on screen.
    @ObservationIgnored private var everSeenAlive = false
    @ObservationIgnored private var sessionEndedEmitted = false

    private struct Outbound: Sendable {
        let commandId: String?
        let data: Data
    }

    private enum PendingCommand {
        case prompt(text: String, images: [ChatComposerImage])
        case setConfig(configId: String)
        case respond(requestId: String)
        case dequeue(queueId: String)
        case cancel
        case restart

        var configId: String? {
            if case .setConfig(let configId) = self { return configId }
            return nil
        }
    }

    init(
        sessionId: String,
        webSocketFactory: WebSocketCoreFactory,
        sessionManager: SessionManager,
        tokenStore: TokenStore,
        foregroundSessionTracker: ForegroundSessionTracker,
        reviewPrompt: ReviewPromptCoordinator
    ) {
        self.sessionId = sessionId
        self.webSocketFactory = webSocketFactory
        self.sessionManager = sessionManager
        self.tokenStore = tokenStore
        self.foregroundSessionTracker = foregroundSessionTracker
        self.reviewPrompt = reviewPrompt
        (outbound, outboundContinuation) = AsyncStream.makeStream(of: Outbound.self)
    }

    // MARK: - Derived state

    var phase: ChatPhase { state.phase }
    var isLive: Bool { connection == .live }
    var isRunning: Bool { phase == .running }
    var isLoading: Bool { !hasSnapshot || phase == .starting || phase == .loading }

    /// Whether commands can reach a running agent.
    var canAct: Bool {
        isLive && !sessionClosed && phase != .error && phase != .stopped
    }

    var hasDraft: Bool {
        !composerText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty
    }

    var canSend: Bool { canAct && hasDraft && preparingImages == 0 }

    var showsStop: Bool { isRunning && !hasDraft }

    var canAttachImages: Bool { state.promptImages && attachments.count + preparingImages < Self.maxImages }

    /// The tool call ids a pending permission request is waiting on.
    var waitingToolIds: Set<String> {
        Set(state.requests.compactMap(\.toolCallId))
    }

    func configValue(_ option: ChatConfigOption) -> ChatConfigValue {
        configOverrides[option.id] ?? option.value
    }

    // MARK: - Lifecycle

    func markVisible() {
        reviewPrompt.terminalVisitStarted()
        screenVisible = true
        foregroundSessionTracker.setVisible(sessionId)
        syncWatch()
    }

    func markHidden() {
        reportReviewVisit()
        screenVisible = false
        foregroundSessionTracker.clearVisible(sessionId)
        syncWatch()
    }

    func setAppActive(_ active: Bool) {
        appActive = active
        syncWatch()
    }

    func markEndedByUser() {
        endedByUser = true
    }

    /// An error the chat screen showed the user, e.g. a failed stop.
    func noteReviewError() {
        reviewPrompt.noteError()
    }

    /// Read before the hide takes effect: a visit ends cleanly when the user
    /// stopped the session or the agent is still reachable as they leave.
    private func reportReviewVisit() {
        let endedCleanly = endedByUser || isReviewHealthyConnection
        reviewPrompt.recordTerminalVisit(healthySeconds: reviewMeter.finish(), endedCleanly: endedCleanly)
        endedByUser = false
    }

    private var isReviewHealthyConnection: Bool {
        isLive && !sessionClosed && phase != .error
    }

    private func syncReviewMeter() {
        reviewMeter.update(healthy: screenVisible && appActive && isReviewHealthyConnection)
    }

    /// Opens the socket at once when the session is read, and detaches after a
    /// grace when it is not: a quick trip to a child screen or another session
    /// keeps the socket, a longer absence costs one sync on return.
    private func syncWatch() {
        syncReviewMeter()
        guard !isTornDown else { return }
        if screenVisible && appActive {
            detachTask?.cancel()
            detachTask = nil
            if core == nil {
                openConnection()
            } else if connection != .live, let core {
                Task { await core.forceReconnect() }
            }
            return
        }
        guard detachTask == nil, core != nil else { return }
        detachTask = Task { [weak self] in
            try? await Task.sleep(for: Self.detachGrace)
            guard !Task.isCancelled, let self else { return }
            self.detachTask = nil
            if self.foregroundSessionTracker.isHeld(self.sessionId) && self.appActive {
                self.syncWatch()
                return
            }
            self.detach()
        }
    }

    private func openConnection() {
        guard !isTornDown, core == nil else { return }
        connection = .connecting
        let core = webSocketFactory.make(
            SessionSocketURL.provider(sessionId: sessionId, tokenStore: tokenStore, sessionManager: sessionManager),
            { [sessionManager] in await sessionManager.isLoggedIn() }
        )
        self.core = core
        eventTask = Task { [weak self] in
            for await event in core.events {
                await self?.handle(event)
            }
        }
        if sendTask == nil {
            sendTask = Task { [weak self, outbound] in
                for await item in outbound {
                    guard let current = self?.core else {
                        self?.sendFailed(item.commandId)
                        continue
                    }
                    self?.armDeadline(item.commandId, frameBytes: item.data.count)
                    if await !current.send(binary: item.data) {
                        self?.sendFailed(item.commandId)
                    }
                }
            }
        }
        Task { await core.connect() }
    }

    private func detach() {
        guard let core else { return }
        self.core = nil
        eventTask?.cancel()
        eventTask = nil
        connection = .detached
        dropInFlight()
        Task { await core.disconnect() }
    }

    func disconnect() {
        isTornDown = true
        cancelDeadlines()
        detachTask?.cancel()
        eventTask?.cancel()
        sendTask?.cancel()
        outboundContinuation.finish()
        connection = .closed
        if screenVisible {
            foregroundSessionTracker.clearVisible(sessionId)
        }
        if let core {
            self.core = nil
            Task { await core.disconnect() }
        }
    }

    /// Feeds the agent's session list in; `true` exactly once when it proves
    /// this session never existed (a stale deep link), so the screen can leave.
    func noteSessionSnapshot(isListed: Bool, synced: Bool) -> Bool {
        if isListed || hasSnapshot {
            everSeenAlive = true
            return false
        }
        guard synced, !everSeenAlive, !sessionEndedEmitted else { return false }
        sessionEndedEmitted = true
        return true
    }

    // MARK: - Inbound

    private func handle(_ event: WebSocketEvent) async {
        switch event {
        case .connected:
            connection = .live
            lastSync = nil
            requestSync(timeline.syncRequest)
        case .binary(let data):
            await handleBinary(data)
        case .disconnected, .tokenRejected, .accountRemoved, .incompatibleServer:
            connectionLost()
        case .message, .error:
            break
        }
    }

    private func handleBinary(_ data: Data) async {
        guard let type = data.first else { return }
        switch type {
        case ChatFrameType.agent:
            let payload = Data(data.dropFirst())
            // Snapshots can reach megabytes; parse those off the main actor.
            let frame = payload.count > 32_768
                ? await Task.detached(priority: .userInitiated) { ChatServerFrame.decode(payload) }.value
                : ChatServerFrame.decode(payload)
            guard let frame, !isTornDown else { return }
            apply(frame)
        case TerminalFrameType.state:
            let status = Self.parseStatus(data.dropFirst())
            if status == "stopped" || status == "crashed" {
                sessionClosed = true
            }
        default:
            break
        }
    }

    private func apply(_ frame: ChatServerFrame) {
        if case .result(let result) = frame {
            handleResult(result)
            return
        }
        let (changes, sync) = timeline.apply(frame)
        if case .snapshot = frame {
            lastSync = nil
            let inFlight = Set(pending.values.compactMap(\.configId))
            configOverrides = configOverrides.filter { inFlight.contains($0.key) }
        }
        if changes.contains(.items) {
            items = timeline.items
            truncated = timeline.truncated
            itemsVersion &+= 1
        }
        if timeline.state != state {
            state = timeline.state
            pruneOptimisticState()
        }
        if !hasSnapshot, timeline.hasSnapshot {
            hasSnapshot = true
        }
        if let sync {
            requestSync(sync)
        }
    }

    /// A sync already asked for is not repeated for a few seconds: frames that
    /// arrive while its answer is on the way would otherwise each ask again.
    private func requestSync(_ request: ChatTimeline.SyncRequest) {
        if let lastSync, lastSync.request == request, Date().timeIntervalSince(lastSync.at) < 3 {
            return
        }
        lastSync = (request, Date())
        enqueue(.sync(id: nextCommandId(), epoch: request.epoch, afterSeq: request.afterSeq), pending: nil)
    }

    private func pruneOptimisticState() {
        let requestIds = Set(state.requests.map(\.id))
        answering = answering.filter { requestIds.contains($0.key) }
        formErrors = formErrors.filter { requestIds.contains($0.key) }
        let queueIds = Set(state.queue.map(\.id))
        dequeuing = dequeuing.filter { queueIds.contains($0) }
        configOverrides = configOverrides.filter { entry in
            state.config.first { $0.id == entry.key }?.value != entry.value
        }
        if restarting, phase != .error {
            restarting = false
        }
    }

    private func handleResult(_ result: ChatResultFrame) {
        guard let id = result.id, let command = takePending(id) else { return }
        switch command {
        case .prompt(let text, let images):
            guard !result.ok else { return }
            restoreDraft(text: text, images: images)
            message = ToastMessage(text: result.error ?? "The message was not sent", tone: .danger)
        case .setConfig(let configId):
            guard !result.ok else { return }
            configOverrides[configId] = nil
            message = ToastMessage(text: result.error ?? "The setting was not changed", tone: .danger)
        case .respond(let requestId):
            // An answered request keeps its spinner until the state drops it;
            // a late duplicate answer counts as answered.
            guard !result.ok, result.error != "This request was already answered" else { return }
            answering[requestId] = nil
            let error = result.error ?? "The answer was not accepted"
            if case .form? = state.requests.first(where: { $0.id == requestId }) {
                formErrors[requestId] = error
            } else {
                message = ToastMessage(text: error, tone: .danger)
            }
        case .dequeue(let queueId):
            guard !result.ok else { return }
            dequeuing.remove(queueId)
            if let error = result.error {
                message = ToastMessage(text: error)
            }
        case .cancel:
            if !result.ok, let error = result.error {
                message = ToastMessage(text: error, tone: .danger)
            }
        case .restart:
            restarting = false
            if !result.ok {
                message = ToastMessage(text: result.error ?? "The agent did not restart", tone: .danger)
            }
        }
    }

    private func connectionLost() {
        if connection != .closed, connection != .detached {
            connection = .connecting
        }
        dropInFlight()
    }

    /// A dropped socket loses the answers to whatever was in flight; the next
    /// sync shows the real state, so the optimistic marks are cleared. Prompts
    /// already handed to the socket go back to the composer, newest first so
    /// the restored draft keeps the sent order; queued ones are sent after the
    /// reconnect.
    private func dropInFlight() {
        let lostPrompts = pending.compactMap { id, command -> String? in
            guard case .prompt = command, deadlines[id] != nil else { return nil }
            return id
        }
        for id in lostPrompts.sorted(by: { Self.commandNumber($0) > Self.commandNumber($1) }) {
            sendFailed(id, promptNotice: Self.connectionDroppedNotice)
        }
        cancelDeadlines()
        answering = [:]
        dequeuing = []
        configOverrides = [:]
        restarting = false
        pending = pending.filter { _, command in
            if case .prompt = command { return true }
            return false
        }
    }

    private func sendFailed(_ commandId: String?, promptNotice: String = "Not sent. Check the connection.") {
        guard let commandId, let command = takePending(commandId) else { return }
        switch command {
        case .prompt(let text, let images):
            restoreDraft(text: text, images: images)
            message = ToastMessage(text: promptNotice, tone: .danger)
        case .setConfig(let configId):
            configOverrides[configId] = nil
        case .respond(let requestId):
            answering[requestId] = nil
        case .dequeue(let queueId):
            dequeuing.remove(queueId)
        case .restart:
            restarting = false
        case .cancel:
            break
        }
    }

    private func takePending(_ commandId: String) -> PendingCommand? {
        deadlines.removeValue(forKey: commandId)?.cancel()
        return pending.removeValue(forKey: commandId)
    }

    /// `NWConnection.send` succeeds once TCP takes the bytes, so on a half-open
    /// socket a command would wait for minutes with no answer.
    private func armDeadline(_ commandId: String?, frameBytes: Int) {
        guard let commandId, !isTornDown, pending[commandId] != nil else { return }
        let timeout = Self.commandTimeout + Self.commandTimeoutPerMegabyte * (Double(frameBytes) / 1_000_000)
        deadlines[commandId]?.cancel()
        deadlines[commandId] = Task { [weak self] in
            try? await Task.sleep(for: timeout)
            guard !Task.isCancelled, let self else { return }
            self.commandTimedOut(commandId)
        }
    }

    /// `forceReconnect()` emits no `.disconnected`, so the loss is applied here.
    private func commandTimedOut(_ commandId: String) {
        guard !isTornDown, pending[commandId] != nil else { return }
        sendFailed(commandId, promptNotice: Self.connectionDroppedNotice)
        connectionLost()
        if let core {
            Task { await core.forceReconnect() }
        }
    }

    private func cancelDeadlines() {
        for task in deadlines.values {
            task.cancel()
        }
        deadlines = [:]
    }

    private func restoreDraft(text: String, images: [ChatComposerImage]) {
        if composerText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            composerText = text
        } else if !text.isEmpty {
            composerText = text + "\n" + composerText
        }
        composerSelection = nil
        var total = 0
        attachments = (images + attachments).prefix(Self.maxImages).filter { image in
            total += image.payload.data.utf8.count
            return total <= Self.maxImagesBase64Total
        }
    }

    // MARK: - Commands

    private func nextCommandId() -> String {
        commandCounter += 1
        return "\(clientId)-\(commandCounter)"
    }

    private static func commandNumber(_ commandId: String) -> Int {
        commandId.split(separator: "-").last.flatMap { Int($0) } ?? 0
    }

    private func enqueue(_ command: ChatCommand, pending entry: PendingCommand?) {
        if let entry {
            pending[command.id] = entry
        }
        outboundContinuation.yield(Outbound(commandId: entry == nil ? nil : command.id, data: command.encoded))
    }

    func send() {
        guard canSend else { return }
        let text = composerText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count <= Self.maxPromptLength else {
            message = ToastMessage(text: "The message is too long", tone: .danger)
            return
        }
        let images = attachments
        composerText = ""
        composerSelection = nil
        attachments = []
        followRequests &+= 1
        let id = nextCommandId()
        enqueue(.prompt(id: id, text: text, images: images.map(\.payload)), pending: .prompt(text: text, images: images))
    }

    func stop() {
        guard isLive, isRunning, !state.cancelling else { return }
        enqueue(.cancel(id: nextCommandId()), pending: .cancel)
    }

    func setConfig(_ option: ChatConfigOption, to value: ChatConfigValue) {
        guard canAct, configValue(option) != value else { return }
        configOverrides[option.id] = value
        enqueue(
            .setConfig(id: nextCommandId(), configId: option.id, value: value.jsonValue),
            pending: .setConfig(configId: option.id)
        )
    }

    func respond(to request: ChatPendingRequest.Permission, option: ChatPermissionOption) {
        guard isLive, answering[request.id] == nil else { return }
        answering[request.id] = option.id
        enqueue(
            .respondPermission(id: nextCommandId(), requestId: request.id, optionId: option.id),
            pending: .respond(requestId: request.id)
        )
    }

    func respond(to request: ChatPendingRequest.Form, action: String, values: [String: JSONValue]?) {
        guard isLive, answering[request.id] == nil else { return }
        answering[request.id] = action
        formErrors[request.id] = nil
        enqueue(
            .respondForm(id: nextCommandId(), requestId: request.id, action: action, values: action == "accept" ? values : nil),
            pending: .respond(requestId: request.id)
        )
    }

    func dequeue(_ queued: ChatQueuedMessage) {
        guard isLive, !dequeuing.contains(queued.id) else { return }
        dequeuing.insert(queued.id)
        enqueue(.dequeue(id: nextCommandId(), queueId: queued.id), pending: .dequeue(queueId: queued.id))
    }

    func restart() {
        guard isLive, phase == .error, !restarting else { return }
        restarting = true
        enqueue(.restart(id: nextCommandId()), pending: .restart)
    }

    func toggleExpanded(_ id: String) {
        if expandedItems.remove(id) == nil {
            expandedItems.insert(id)
        }
    }

    // MARK: - Composer

    func insertTranscript(_ transcript: String) {
        let clean = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        insertAtCursor(clean, spaced: true)
    }

    private func insertAtCursor(_ text: String, spaced: Bool) {
        let length = composerText.count
        let selection = composerSelection ?? length..<length
        let lower = min(max(selection.lowerBound, 0), length)
        let upper = min(max(selection.upperBound, lower), length)
        let before = String(composerText.prefix(lower))
        let after = String(composerText.dropFirst(upper))
        let lead = spaced ? (before.last.map { $0.isWhitespace ? "" : " " } ?? "") : ""
        let trail = spaced ? (after.first.map { $0.isWhitespace ? "" : " " } ?? "") : ""
        let inserted = lead + text + trail
        composerText = before + inserted + after
        if #available(iOS 18.0, *) {
            let cursor = before.count + inserted.count
            composerSelection = cursor..<cursor
            composerCursorRequest += 1
        }
    }

    /// Replaces the composer with `/name ` for a picked slash command.
    func applySlashCommand(_ command: ChatSlashCommand) {
        composerText = "/\(command.name) "
        composerSelection = nil
        if #available(iOS 18.0, *) {
            composerSelection = composerText.count..<composerText.count
            composerCursorRequest += 1
        }
    }

    func addImage(data: Data) {
        guard canAttachImages else { return }
        preparingImages += 1
        let limit = Self.maxImageBase64
        Task { [weak self] in
            let prepared = await Task.detached(priority: .userInitiated) {
                ChatImageEncoder.prepare(data, maxBase64: limit)
            }.value
            guard let self else { return }
            self.preparingImages -= 1
            guard let prepared else {
                self.message = ToastMessage(text: "This image could not be attached", tone: .danger)
                return
            }
            guard self.attachments.count < Self.maxImages else { return }
            let total = self.attachments.reduce(prepared.payload.data.utf8.count) { $0 + $1.payload.data.utf8.count }
            guard total <= Self.maxImagesBase64Total else {
                self.message = ToastMessage(text: "These images are too large to send together", tone: .danger)
                return
            }
            self.attachments.append(ChatComposerImage(payload: prepared.payload, thumbnail: prepared.thumbnail))
        }
    }

    func removeImage(_ id: UUID) {
        attachments.removeAll { $0.id == id }
    }

    private static func parseStatus(_ payload: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else { return nil }
        return object["status"] as? String
    }
}

extension ChatSessionModel {
    func insertTemplate(_ template: TemplateDto) {
        insertAtCursor(template.body, spaced: false)
    }
}

/// Downscales a picked image to the prompt limits: longest side 1568 px, JPEG,
/// quality lowered until the base64 fits.
enum ChatImageEncoder {
    struct Prepared: Sendable {
        let payload: ChatImagePayload
        let thumbnail: UIImage
    }

    static let maxSide: CGFloat = 1568

    static func prepare(_ data: Data, maxBase64: Int) -> Prepared? {
        guard case .image(let image) = ChatImageDecoder.decode(data, maxPixelSize: Int(maxSide), maxSourcePixels: nil) else {
            return nil
        }
        let scaled = resized(image, maxSide: maxSide)
        var quality: CGFloat = 0.8
        var jpeg = scaled.jpegData(compressionQuality: quality)
        while let current = jpeg, (current.count + 2) / 3 * 4 > maxBase64, quality > 0.25 {
            quality -= 0.15
            jpeg = scaled.jpegData(compressionQuality: quality)
        }
        guard let jpeg, (jpeg.count + 2) / 3 * 4 <= maxBase64 else { return nil }
        return Prepared(
            payload: ChatImagePayload(mime: "image/jpeg", data: jpeg.base64EncodedString()),
            thumbnail: resized(scaled, maxSide: 160)
        )
    }

    private static func resized(_ image: UIImage, maxSide: CGFloat) -> UIImage {
        let size = image.size
        let longest = max(size.width, size.height)
        let scale = longest > maxSide ? maxSide / longest : 1
        let target = CGSize(width: max(1, (size.width * scale).rounded()), height: max(1, (size.height * scale).rounded()))
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: target, format: format).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(origin: .zero, size: target))
            image.draw(in: CGRect(origin: .zero, size: target))
        }
    }
}
