import Foundation

protocol Sleeper: Sendable {
    func sleep(for duration: Duration) async throws
}

struct ContinuousSleeper: Sleeper {
    func sleep(for duration: Duration) async throws {
        try await Task.sleep(for: duration)
    }
}

enum WebSocketTransportError: Error, Equatable, Sendable {
    /// The HTTP upgrade was rejected before the WebSocket opened. A 401 here
    /// is treated like close code 4001 (token rejected). Network.framework
    /// cannot always surface the HTTP status; nil means "rejected, status
    /// unknown" and falls through to the normal reconnect path.
    case upgradeRejected(statusCode: Int?)
    case network(String)
}

enum WebSocketTransportEvent: Sendable {
    case opened
    case text(String)
    case binary(Data)
    case closed(code: UInt16, reason: String)
    case failed(WebSocketTransportError)
}

protocol WebSocketTransportConnection: Sendable {
    var events: AsyncStream<WebSocketTransportEvent> { get }
    func start()
    func send(text: String) async -> Bool
    func send(binary: Data) async -> Bool
    func close(code: UInt16, reason: String)
    func cancel()
}

protocol WebSocketTransportFactory: Sendable {
    func makeConnection(url: URL) -> any WebSocketTransportConnection
}
