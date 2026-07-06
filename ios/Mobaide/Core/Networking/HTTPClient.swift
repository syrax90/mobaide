import Foundation

struct HTTPClient: Sendable {
    let session: URLSession

    init(session: URLSession = HTTPClient.makeSession()) {
        self.session = session
    }

    /// 30 s request timeout mirrors the OkHttp connect/read/write timeouts.
    static func makeSession(
        timeout: TimeInterval = 30,
        protocolClasses: [AnyClass]? = nil
    ) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = timeout
        if let protocolClasses {
            configuration.protocolClasses = protocolClasses
        }
        return URLSession(configuration: configuration)
    }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError(code: -1, errorBody: "Non-HTTP response")
        }
        return (data, http)
    }
}
