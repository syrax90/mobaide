import Foundation
import Network

struct NWWebSocketTransportFactory: WebSocketTransportFactory {
    let pingInterval: Duration
    let sleeper: any Sleeper

    init(pingInterval: Duration = .seconds(30), sleeper: any Sleeper = ContinuousSleeper()) {
        self.pingInterval = pingInterval
        self.sleeper = sleeper
    }

    func makeConnection(url: URL) -> any WebSocketTransportConnection {
        NWWebSocketConnection(url: url, pingInterval: pingInterval, sleeper: sleeper)
    }
}

/// Network.framework WebSocket transport.
///
/// - Network.framework has no built-in ping interval (OkHttp's
///   `pingInterval(30s)` equivalent), so a client-driven ping task sends a
///   ping frame every `pingInterval`.
/// - Server close frames can arrive either as receive-loop metadata
///   (`opcode == .close`) or as a connection failure; both paths emit exactly
///   one terminal event on the stream.
/// - A rejected HTTP upgrade surfaces as an NW error without the HTTP status,
///   so it maps to `.network` (status unknown) rather than `.upgradeRejected` —
///   the server's primary auth-rejection path is accept-then-close-4001,
///   which arrives via close metadata and keeps its code.
final class NWWebSocketConnection: WebSocketTransportConnection, @unchecked Sendable {
    let events: AsyncStream<WebSocketTransportEvent>

    private let connection: NWConnection
    private let continuation: AsyncStream<WebSocketTransportEvent>.Continuation
    private let queue = DispatchQueue(label: "com.mobaide.app.websocket")
    private let pingInterval: Duration
    private let sleeper: any Sleeper
    private let lock = NSLock()
    private var pingTask: Task<Void, Never>?
    private var finished = false

    init(url: URL, pingInterval: Duration, sleeper: any Sleeper) {
        self.pingInterval = pingInterval
        self.sleeper = sleeper

        let parameters: NWParameters = url.scheme == "wss" ? .tls : .tcp
        let wsOptions = NWProtocolWebSocket.Options()
        wsOptions.autoReplyPing = true
        parameters.defaultProtocolStack.applicationProtocols.insert(wsOptions, at: 0)
        connection = NWConnection(to: .url(url), using: parameters)
        (events, continuation) = AsyncStream.makeStream(of: WebSocketTransportEvent.self)
    }

    func start() {
        connection.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                self.continuation.yield(.opened)
                self.receiveLoop()
                self.startPing()
            case .failed(let error):
                self.emitFailureAndFinish(error)
            case .waiting(let error):
                // NWConnection retries forever in .waiting; the reconnect
                // policy lives in WebSocketCore, so fail fast like OkHttp.
                self.connection.cancel()
                self.emitFailureAndFinish(error)
            case .cancelled:
                self.finish()
            default:
                break
            }
        }
        connection.start(queue: queue)
    }

    func send(text: String) async -> Bool {
        await sendMessage(content: Data(text.utf8), opcode: .text, identifier: "text")
    }

    func send(binary: Data) async -> Bool {
        await sendMessage(content: binary, opcode: .binary, identifier: "binary")
    }

    func close(code: UInt16, reason: String) {
        cancelPing()
        let metadata = NWProtocolWebSocket.Metadata(opcode: .close)
        if let defined = NWProtocolWebSocket.CloseCode.Defined(rawValue: code) {
            metadata.closeCode = .protocolCode(defined)
        } else {
            metadata.closeCode = .privateCode(code)
        }
        let context = NWConnection.ContentContext(identifier: "close", metadata: [metadata])
        let payload = reason.isEmpty ? nil : Data(reason.utf8)
        connection.send(
            content: payload,
            contentContext: context,
            isComplete: true,
            completion: .contentProcessed { [weak self] _ in
                self?.connection.cancel()
            }
        )
    }

    func cancel() {
        cancelPing()
        connection.cancel()
    }

    private func sendMessage(content: Data, opcode: NWProtocolWebSocket.Opcode, identifier: String) async -> Bool {
        await withCheckedContinuation { done in
            let metadata = NWProtocolWebSocket.Metadata(opcode: opcode)
            let context = NWConnection.ContentContext(identifier: identifier, metadata: [metadata])
            connection.send(
                content: content,
                contentContext: context,
                isComplete: true,
                completion: .contentProcessed { error in
                    done.resume(returning: error == nil)
                }
            )
        }
    }

    private func receiveLoop() {
        connection.receiveMessage { [weak self] data, context, _, error in
            guard let self else { return }
            if let metadata = context?.protocolMetadata(definition: NWProtocolWebSocket.definition)
                as? NWProtocolWebSocket.Metadata {
                switch metadata.opcode {
                case .close:
                    let code: UInt16
                    switch metadata.closeCode {
                    case .protocolCode(let defined):
                        code = UInt16(defined.rawValue)
                    case .applicationCode(let raw), .privateCode(let raw):
                        code = raw
                    @unknown default:
                        code = 1005
                    }
                    let reason = data.map { String(decoding: $0, as: UTF8.self) } ?? ""
                    self.emitCloseAndFinish(code: code, reason: reason)
                    self.connection.cancel()
                    return
                case .text:
                    self.continuation.yield(.text(String(decoding: data ?? Data(), as: UTF8.self)))
                case .binary:
                    self.continuation.yield(.binary(data ?? Data()))
                default:
                    break
                }
            }
            if let error {
                self.emitFailureAndFinish(error)
                self.connection.cancel()
                return
            }
            self.receiveLoop()
        }
    }

    private func startPing() {
        let task = Task { [weak self, sleeper, pingInterval] in
            while !Task.isCancelled {
                guard (try? await sleeper.sleep(for: pingInterval)) != nil, !Task.isCancelled else { return }
                self?.sendPing()
            }
        }
        lock.withLock { pingTask = task }
    }

    private func sendPing() {
        let metadata = NWProtocolWebSocket.Metadata(opcode: .ping)
        metadata.setPongHandler(queue) { _ in }
        let context = NWConnection.ContentContext(identifier: "ping", metadata: [metadata])
        connection.send(content: nil, contentContext: context, isComplete: true, completion: .contentProcessed { _ in })
    }

    private func cancelPing() {
        lock.withLock {
            pingTask?.cancel()
            pingTask = nil
        }
    }

    private func emitCloseAndFinish(code: UInt16, reason: String) {
        guard markFinished() else { return }
        continuation.yield(.closed(code: code, reason: reason))
        continuation.finish()
        cancelPing()
    }

    private func emitFailureAndFinish(_ error: NWError) {
        guard markFinished() else { return }
        continuation.yield(.failed(.network(String(describing: error))))
        continuation.finish()
        cancelPing()
    }

    private func finish() {
        guard markFinished() else { return }
        continuation.finish()
        cancelPing()
    }

    /// Exactly-one-terminal-event guard across the receive-metadata close and
    /// connection-failure races; true only for the first caller.
    private func markFinished() -> Bool {
        lock.withLock {
            let wasFinished = finished
            finished = true
            return !wasFinished
        }
    }
}
