import Foundation

/// Scriptable URLProtocol: tests enqueue (status, body) responses and assert
/// against the recorded requests (including drained bodies, which URLSession
/// exposes only as a stream inside URLProtocol).
final class StubURLProtocol: URLProtocol {
    struct StubResponse {
        let status: Int
        let body: Data
    }

    private static let lock = NSLock()
    private nonisolated(unsafe) static var queue: [StubResponse] = []
    private nonisolated(unsafe) static var recordedRequests: [URLRequest] = []
    private nonisolated(unsafe) static var recordedBodies: [Data] = []
    private nonisolated(unsafe) static var onRequestStorage: (@Sendable (URLRequest) -> Void)?

    /// Fires on the URLSession loading thread after the request is recorded
    /// and strictly before its response is delivered — the deterministic seam
    /// for mutating app state mid-flight (request issued, response not yet
    /// applied). Cleared by `reset()`.
    static var onRequest: (@Sendable (URLRequest) -> Void)? {
        get { lock.withLock { onRequestStorage } }
        set { lock.withLock { onRequestStorage = newValue } }
    }

    static func reset() {
        lock.withLock {
            queue = []
            recordedRequests = []
            recordedBodies = []
            onRequestStorage = nil
        }
    }

    static func enqueue(status: Int, body: String) {
        lock.withLock {
            queue.append(StubResponse(status: status, body: Data(body.utf8)))
        }
    }

    static var requests: [URLRequest] {
        lock.withLock { recordedRequests }
    }

    static var bodies: [Data] {
        lock.withLock { recordedBodies }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let body = Self.drainBody(from: request)
        let response = Self.lock.withLock { () -> StubResponse in
            Self.recordedRequests.append(request)
            Self.recordedBodies.append(body)
            return Self.queue.isEmpty
                ? StubResponse(status: 200, body: Data("{}".utf8))
                : Self.queue.removeFirst()
        }
        Self.onRequest?(request)
        let http = HTTPURLResponse(
            url: request.url!,
            statusCode: response.status,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: http, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: response.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    private static func drainBody(from request: URLRequest) -> Data {
        if let body = request.httpBody {
            return body
        }
        guard let stream = request.httpBodyStream else {
            return Data()
        }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let bufferSize = 4096
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufferSize)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let read = stream.read(buffer, maxLength: bufferSize)
            guard read > 0 else { break }
            data.append(buffer, count: read)
        }
        return data
    }
}
