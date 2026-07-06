import Foundation

enum WebSocketEvent: Equatable, Sendable {
    case connected
    case disconnected
    case message(String)
    case binary(Data)
    case error(String)
    /// Close code 4001, or HTTP 401 during the upgrade. Terminal: reconnect stops.
    case tokenRejected
    /// Close code 4010. Terminal: reconnect stops.
    case accountRemoved
    /// Close code 4002. Terminal: reconnect stops.
    case incompatibleServer(reason: String)
}

/// Reconnecting WebSocket state machine ported from Android
/// `WebSocketConnection` (WebSocketManager.kt):
///
/// - Backoff starts at 1 s; each scheduled reconnect sleeps the *current*
///   value, then doubles it (capped at 30 s). A successful open and
///   `forceReconnect()` reset it to 1 s.
/// - A generation counter (incremented only by `forceReconnect()`) drops every
///   event from a superseded transport, so a stale socket cannot emit a
///   phantom `.disconnected` or trigger a reconnect.
/// - Close codes 4001/4010/4002 (and HTTP 401 at upgrade ≡ 4001) are terminal;
///   everything else — including 4011 admin-force-disconnect and 4012
///   agent-transient-offline — falls through to the backoff reconnect loop.
///   `.disconnected` is always emitted before the terminal event.
/// - `urlProvider` is re-evaluated on every (re)connect attempt; returning nil
///   emits `.disconnected` without scheduling a reconnect, throwing emits
///   `.error`. Reconnects are additionally gated on `isAuthenticated()`.
actor WebSocketCore {
    static let initialBackoff: Duration = .milliseconds(1000)
    static let maxBackoff: Duration = .milliseconds(30000)

    static let authFailedCloseCode: UInt16 = 4001
    static let protocolIncompatibleCloseCode: UInt16 = 4002
    static let adminRevokedCloseCode: UInt16 = 4010
    static let normalCloseCode: UInt16 = 1000

    nonisolated let events: AsyncStream<WebSocketEvent>

    private let urlProvider: @Sendable () async throws -> String?
    private let transportFactory: any WebSocketTransportFactory
    private let isAuthenticated: @Sendable () async -> Bool
    private let sleeper: any Sleeper
    private let eventSink: AsyncStream<WebSocketEvent>.Continuation

    private var connection: (any WebSocketTransportConnection)?
    private var consumeTask: Task<Void, Never>?
    private var reconnectTask: Task<Void, Never>?
    private var shouldReconnect = true
    private var generation = 0
    private var backoff = WebSocketCore.initialBackoff

    init(
        urlProvider: @escaping @Sendable () async throws -> String?,
        transportFactory: any WebSocketTransportFactory,
        isAuthenticated: @escaping @Sendable () async -> Bool = { true },
        sleeper: any Sleeper = ContinuousSleeper()
    ) {
        self.urlProvider = urlProvider
        self.transportFactory = transportFactory
        self.isAuthenticated = isAuthenticated
        self.sleeper = sleeper
        (events, eventSink) = AsyncStream.makeStream(of: WebSocketEvent.self)
    }

    func connect() async {
        shouldReconnect = true
        await doConnect()
    }

    func disconnect() {
        shouldReconnect = false
        reconnectTask?.cancel()
        reconnectTask = nil
        consumeTask?.cancel()
        consumeTask = nil
        connection?.close(code: Self.normalCloseCode, reason: "Client disconnect")
        connection = nil
    }

    func forceReconnect() async {
        guard shouldReconnect, await isAuthenticated() else { return }
        generation += 1
        reconnectTask?.cancel()
        reconnectTask = nil
        backoff = Self.initialBackoff
        connection?.close(code: Self.normalCloseCode, reason: "Force reconnect")
        connection = nil
        guard shouldReconnect, await isAuthenticated() else { return }
        await doConnect()
    }

    @discardableResult
    func send(text: String) async -> Bool {
        guard let connection, await connection.send(text: text) else {
            eventSink.yield(.error("WebSocket send failed"))
            return false
        }
        return true
    }

    @discardableResult
    func send(binary: Data) async -> Bool {
        guard let connection, await connection.send(binary: binary) else {
            eventSink.yield(.error("WebSocket send failed"))
            return false
        }
        return true
    }

    private func doConnect() async {
        let urlString: String?
        do {
            urlString = try await urlProvider()
        } catch {
            eventSink.yield(.error(String(describing: error)))
            return
        }
        guard let urlString, let url = URL(string: urlString) else {
            eventSink.yield(.disconnected)
            return
        }
        let transport = transportFactory.makeConnection(url: url)
        connection = transport
        let myGeneration = generation
        consumeTask = Task { [weak self] in
            for await event in transport.events {
                guard let self else { return }
                await self.handle(event, from: myGeneration)
            }
        }
        transport.start()
    }

    private func handle(_ event: WebSocketTransportEvent, from myGeneration: Int) async {
        guard myGeneration == generation else { return }
        switch event {
        case .opened:
            backoff = Self.initialBackoff
            eventSink.yield(.connected)
        case .text(let text):
            eventSink.yield(.message(text))
        case .binary(let data):
            eventSink.yield(.binary(data))
        case .closed(let code, let reason):
            eventSink.yield(.disconnected)
            switch code {
            case Self.authFailedCloseCode:
                terminate(with: .tokenRejected)
            case Self.adminRevokedCloseCode:
                terminate(with: .accountRemoved)
            case Self.protocolIncompatibleCloseCode:
                let message = reason.isEmpty ? "Server protocol incompatible" : reason
                terminate(with: .incompatibleServer(reason: message))
            default:
                await scheduleReconnect()
            }
        case .failed(let error):
            eventSink.yield(.error(String(describing: error)))
            eventSink.yield(.disconnected)
            if case .upgradeRejected(let statusCode) = error, statusCode == 401 {
                terminate(with: .tokenRejected)
            } else {
                await scheduleReconnect()
            }
        }
    }

    private func terminate(with event: WebSocketEvent) {
        shouldReconnect = false
        reconnectTask?.cancel()
        reconnectTask = nil
        eventSink.yield(event)
    }

    private func scheduleReconnect() async {
        guard shouldReconnect, await isAuthenticated() else { return }
        reconnectTask?.cancel()
        let delay = backoff
        reconnectTask = Task { [weak self, sleeper] in
            do {
                try await sleeper.sleep(for: delay)
            } catch {
                return
            }
            guard !Task.isCancelled else { return }
            await self?.reconnectAfterBackoff()
        }
    }

    private func reconnectAfterBackoff() async {
        backoff = min(backoff * 2, Self.maxBackoff)
        guard shouldReconnect, await isAuthenticated() else { return }
        await doConnect()
    }
}
