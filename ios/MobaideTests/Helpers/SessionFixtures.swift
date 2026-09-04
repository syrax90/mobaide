import Foundation
@testable import Mobaide

enum TestJWT {
    /// Unsigned JWT with the given `exp` claim (or none) — enough for the
    /// local `exp` parse, which never verifies signatures.
    static func token(exp: Int64?) -> String {
        let header = base64URL(#"{"alg":"none"}"#)
        let payload: String
        if let exp {
            payload = base64URL(#"{"exp":\#(exp)}"#)
        } else {
            payload = base64URL(#"{"sub":"user"}"#)
        }
        return "\(header).\(payload).sig"
    }

    static func base64URL(_ json: String) -> String {
        Data(json.utf8).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// Lock-guarded call counter for closure-injected collaborators invoked off
/// the test's actor (a plain `var` captured in a `@Sendable` closure will not
/// compile under strict concurrency).
final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    var value: Int { lock.withLock { count } }

    func increment() {
        lock.withLock { count += 1 }
    }
}

/// Reusable one-shot barrier: N waiters block until `open()`.
final class AsyncGate: @unchecked Sendable {
    private let lock = NSLock()
    private var opened = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        await withCheckedContinuation { continuation in
            let passThrough = lock.withLock { () -> Bool in
                if opened { return true }
                waiters.append(continuation)
                return false
            }
            if passThrough {
                continuation.resume()
            }
        }
    }

    func open() {
        let resumable = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
            opened = true
            defer { waiters = [] }
            return waiters
        }
        for continuation in resumable {
            continuation.resume()
        }
    }
}

/// Counting, optionally gated fake for `SessionManager.RefreshTransport`.
final class FakeRefreshTransport: @unchecked Sendable {
    enum Behavior {
        case respond(status: Int, body: String)
        case fail(any Error)
    }

    private let lock = NSLock()
    private var callCountStorage = 0
    private var requestsStorage: [URLRequest] = []

    var behavior: Behavior
    /// When set, every call blocks on the gate before responding.
    var gate: AsyncGate?

    init(behavior: Behavior = .respond(
        status: 200,
        body: #"{"access_token":"new-access","refresh_token":"new-refresh"}"#
    )) {
        self.behavior = behavior
    }

    var callCount: Int { lock.withLock { callCountStorage } }
    var requests: [URLRequest] { lock.withLock { requestsStorage } }

    func transport() -> SessionManager.RefreshTransport {
        { [self] request in
            lock.withLock {
                callCountStorage += 1
                requestsStorage.append(request)
            }
            if let gate {
                await gate.wait()
            }
            switch behavior {
            case .respond(let status, let body):
                let response = HTTPURLResponse(
                    url: request.url ?? URL(string: "https://unit.test")!,
                    statusCode: status,
                    httpVersion: "HTTP/1.1",
                    headerFields: nil
                )!
                return (Data(body.utf8), response)
            case .fail(let error):
                throw error
            }
        }
    }
}

/// Fully-wired SessionManager over in-memory keychains.
struct SessionHarness {
    let agentKeychain = InMemoryKeychainStore()
    let userKeychain = InMemoryKeychainStore()
    let refreshTransport: FakeRefreshTransport
    let tokenStore: TokenStore
    let userIdentityStore: UserIdentityStore
    let preferencesStore: PreferencesStore
    let sessionManager: SessionManager

    /// A store over a unique UserDefaults suite so harnesses never bleed
    /// persisted state into each other. Not
    /// `TerminalTestSupport.isolatedPreferencesStore()`, which is `@MainActor`
    /// and so unusable as a default argument in these non-isolated tests.
    static func isolatedPreferencesStore() -> PreferencesStore {
        PreferencesStore(defaults: UserDefaults(suiteName: "session-tests-\(UUID().uuidString)")!)
    }

    init(
        userLoggedIn: Bool = false,
        agentConnected: Bool = false,
        accessTokenExp: Int64? = nil,
        transport: FakeRefreshTransport = FakeRefreshTransport(),
        preferencesStore: PreferencesStore = SessionHarness.isolatedPreferencesStore(),
        now: Date = Date()
    ) {
        refreshTransport = transport
        self.preferencesStore = preferencesStore
        tokenStore = TokenStore(keychain: agentKeychain, now: { now })
        userIdentityStore = UserIdentityStore(keychain: userKeychain)
        if userLoggedIn {
            userIdentityStore.saveUserSession(
                accessToken: "user-token",
                email: "user@example.com",
                userId: "u1",
                role: "user"
            )
        }
        if agentConnected {
            tokenStore.saveAgentUrl("https://agent.example.com")
            let exp = accessTokenExp ?? (Int64(now.timeIntervalSince1970) + 3600)
            tokenStore.saveTokens(accessToken: TestJWT.token(exp: exp), refreshToken: "refresh-1")
        }
        sessionManager = SessionManager(
            tokenStore: tokenStore,
            userIdentityStore: userIdentityStore,
            preferencesStore: preferencesStore,
            refreshTransport: transport.transport()
        )
    }
}

// MARK: - Recording bus hooks

final class RecordingAgentStatusApplier: AgentStatusApplying, @unchecked Sendable {
    private let lock = NSLock()
    private var updatesStorage: [(agentId: String, online: Bool)] = []
    private var refreshCountStorage = 0
    private var clearCountStorage = 0

    var updates: [(agentId: String, online: Bool)] { lock.withLock { updatesStorage } }
    var refreshCount: Int { lock.withLock { refreshCountStorage } }
    var clearCount: Int { lock.withLock { clearCountStorage } }

    func updateAgentOnline(agentId: String, online: Bool) async {
        lock.withLock { updatesStorage.append((agentId, online)) }
    }

    func refreshAgents() async throws {
        lock.withLock { refreshCountStorage += 1 }
    }

    func clear() async {
        lock.withLock { clearCountStorage += 1 }
    }
}

final class RecordingTemplateSync: TemplateSyncApplying, @unchecked Sendable {
    private let lock = NSLock()
    private var upsertsStorage: [TemplateDto] = []
    private var deletesStorage: [String] = []
    private var refreshCountStorage = 0
    private var clearCountStorage = 0

    var upserts: [TemplateDto] { lock.withLock { upsertsStorage } }
    var deletes: [String] { lock.withLock { deletesStorage } }
    var refreshCount: Int { lock.withLock { refreshCountStorage } }
    var clearCount: Int { lock.withLock { clearCountStorage } }

    func applyUpserted(_ template: TemplateDto) async {
        lock.withLock { upsertsStorage.append(template) }
    }

    func applyDeleted(templateId: String) async {
        lock.withLock { deletesStorage.append(templateId) }
    }

    func refreshTemplates() async throws {
        lock.withLock { refreshCountStorage += 1 }
    }

    func clear() async {
        lock.withLock { clearCountStorage += 1 }
    }
}

final class RecordingShortcutSync: ShortcutSyncApplying, @unchecked Sendable {
    private let lock = NSLock()
    private var upsertsStorage: [ShortcutDto] = []
    private var deletesStorage: [String] = []
    private var purgesStorage: [String] = []
    private var refreshCountStorage = 0
    private var clearCountStorage = 0

    var upserts: [ShortcutDto] { lock.withLock { upsertsStorage } }
    var deletes: [String] { lock.withLock { deletesStorage } }
    var purges: [String] { lock.withLock { purgesStorage } }
    var refreshCount: Int { lock.withLock { refreshCountStorage } }
    var clearCount: Int { lock.withLock { clearCountStorage } }

    func applyUpserted(_ shortcut: ShortcutDto) async {
        lock.withLock { upsertsStorage.append(shortcut) }
    }

    func applyDeleted(shortcutId: String) async {
        lock.withLock { deletesStorage.append(shortcutId) }
    }

    func purgeKind(_ kind: String) async {
        lock.withLock { purgesStorage.append(kind) }
    }

    func refreshShortcuts() async throws {
        lock.withLock { refreshCountStorage += 1 }
    }

    func clear() async {
        lock.withLock { clearCountStorage += 1 }
    }
}

final class RecordingCustomTypeSync: CustomTypeSyncApplying, @unchecked Sendable {
    private let lock = NSLock()
    private var upsertsStorage: [CustomAgentTypeDto] = []
    private var deletesStorage: [String] = []
    private var refreshCountStorage = 0
    private var clearCountStorage = 0

    var upserts: [CustomAgentTypeDto] { lock.withLock { upsertsStorage } }
    var deletes: [String] { lock.withLock { deletesStorage } }
    var refreshCount: Int { lock.withLock { refreshCountStorage } }
    var clearCount: Int { lock.withLock { clearCountStorage } }

    func applyUpserted(_ type: CustomAgentTypeDto) async {
        lock.withLock { upsertsStorage.append(type) }
    }

    func applyDeleted(typeId: String) async {
        lock.withLock { deletesStorage.append(typeId) }
    }

    func refreshCustomTypes() async throws {
        lock.withLock { refreshCountStorage += 1 }
    }

    func clear() async {
        lock.withLock { clearCountStorage += 1 }
    }
}

final class RecordingFirstAgentPairedApplier: FirstAgentPairedApplying, @unchecked Sendable {
    private let lock = NSLock()
    private var pairedStorage: [String] = []
    private var reconcileCountStorage = 0

    var paired: [String] { lock.withLock { pairedStorage } }
    var reconcileCount: Int { lock.withLock { reconcileCountStorage } }

    func firstAgentPaired(agentId: String) async {
        lock.withLock { pairedStorage.append(agentId) }
    }

    func reconcileAfterConnect() async {
        lock.withLock { reconcileCountStorage += 1 }
    }
}

// MARK: - Envelope builders

enum EnvelopeFixtures {
    private struct Envelope: Encodable {
        let type: String
        let payload: JSONValue
    }

    /// Wrap a fixture `wire` payload into a `{type, payload}` envelope.
    static func envelope(type: String, payload: JSONValue) throws -> String {
        let data = try WireJSON.encoder().encode(Envelope(type: type, payload: payload))
        return String(decoding: data, as: UTF8.self)
    }

    static func cases(fixture path: String) throws -> [DTOFixtureCase] {
        try FixtureLoader.load(DTOFixtureFile.self, path: path).cases
    }
}
