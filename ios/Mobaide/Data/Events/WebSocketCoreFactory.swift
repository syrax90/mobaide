import Foundation

/// Connection-state surface of an event bus
/// (Android `AgentEventBus.ConnState` parity).
enum BusConnectionState: Equatable, Sendable {
    case disconnected
    case connecting
    case connected
}

/// Builds one `WebSocketCore` per connection cycle. `WebSocketCore.events`
/// is a single-consumer stream that cannot be re-iterated after its
/// consumer is cancelled, so the buses create a fresh core on every
/// start/stop cycle instead of reusing one.
struct WebSocketCoreFactory: Sendable {
    let make: @Sendable (
        _ urlProvider: @escaping @Sendable () async throws -> String?,
        _ isAuthenticated: @escaping @Sendable () async -> Bool
    ) -> WebSocketCore

    static func live(
        transportFactory: any WebSocketTransportFactory = NWWebSocketTransportFactory(),
        sleeper: any Sleeper = ContinuousSleeper()
    ) -> WebSocketCoreFactory {
        WebSocketCoreFactory { urlProvider, isAuthenticated in
            WebSocketCore(
                urlProvider: urlProvider,
                transportFactory: transportFactory,
                isAuthenticated: isAuthenticated,
                sleeper: sleeper
            )
        }
    }
}
