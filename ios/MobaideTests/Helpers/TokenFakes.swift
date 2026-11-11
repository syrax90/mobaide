import Foundation
@testable import Mobaide

final class FakeAgentTokens: AgentTokenProviding, @unchecked Sendable {
    private let lock = NSLock()
    private var token: AgentToken?
    private var refreshResult: String?
    private var validCalls = 0
    private var refreshCalls = 0

    init(token: AgentToken? = AgentToken(value: "agent-token"), refreshResult: String? = nil) {
        self.token = token
        self.refreshResult = refreshResult
    }

    var validTokenCallCount: Int { lock.withLock { validCalls } }
    var refreshedTokenCallCount: Int { lock.withLock { refreshCalls } }

    func validToken() async -> AgentToken? {
        lock.withLock {
            validCalls += 1
            return token
        }
    }

    func refreshedToken() async -> String? {
        lock.withLock {
            refreshCalls += 1
            return refreshResult
        }
    }
}

final class FakeSessionInvalidator: SessionInvalidating, @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var invalidationCount: Int { lock.withLock { count } }

    func invalidateUserSession() async {
        lock.withLock { count += 1 }
    }
}
