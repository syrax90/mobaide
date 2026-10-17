import Foundation

/// The agent's `capabilities.fs`: `dirs` gates suggestions, `windowsPaths` words the hints.
struct FsSupport: Equatable, Sendable {
    let dirs: Bool
    let windowsPaths: Bool

    static let none = FsSupport(dirs: false, windowsPaths: false)
}

/// Folder listings behind the host-path fields (Android
/// `PathSuggestionRepository`), cached per agent so the project form and the
/// session wizard share what either has already fetched.
@MainActor
final class PathSuggestionRepository {
    private struct Support {
        let support: FsSupport
        let probedAt: ContinuousClock.Instant
    }

    private static let supportTTL: Duration = .seconds(5 * 60)
    /// A relative, missing or unreadable directory: remembered as empty so
    /// further keystrokes under it stay local. A timed-out scan (503) or a network
    /// failure is not, and is retried on the next directory change.
    private static let definitiveFailures: Set<Int> = [400, 403, 404]

    private let agentAPI: AgentAPI
    private let tokenStore: TokenStore
    private let now: () -> ContinuousClock.Instant
    private var caches: [String: DirListingCache] = [:]
    private var support: [String: Support] = [:]

    init(
        agentAPI: AgentAPI,
        tokenStore: TokenStore,
        now: @escaping () -> ContinuousClock.Instant = { ContinuousClock.now }
    ) {
        self.agentAPI = agentAPI
        self.tokenStore = tokenStore
        self.now = now
    }

    private var agentKey: String { tokenStore.getAgentScopeKey() ?? "" }

    /// `capabilities.fs`, probed at most every five minutes per agent; `.none` on failure.
    func fsSupport() async -> FsSupport {
        let key = agentKey
        if let cached = support[key], now() - cached.probedAt < Self.supportTTL {
            return cached.support
        }
        guard let info = try? await agentAPI.getAgentVersion() else { return .none }
        let probed = FsSupport(dirs: info.fsCapability("dirs"), windowsPaths: info.fsCapability("windows_paths"))
        support[key] = Support(support: probed, probedAt: now())
        return probed
    }

    /// `capabilities.fs.dirs`, with `fsSupport()`'s caching.
    func isSupported() async -> Bool {
        await fsSupport().dirs
    }

    func lookup(_ query: PathCompletion.Query) -> ListingHit? {
        caches[agentKey, default: DirListingCache(now: now)].lookup(dir: query.dir, fragment: query.fragment)
    }

    func nextRequest(_ query: PathCompletion.Query) -> ListingRequest? {
        caches[agentKey, default: DirListingCache(now: now)].nextRequest(query)
    }

    /// Fetch and cache one listing; false when nothing new was cached.
    func fetch(_ request: ListingRequest) async -> Bool {
        let key = agentKey
        let listing: FsDirsResponse
        do {
            listing = try await agentAPI.listDirs(path: request.dir, q: request.q)
        } catch let error as APIError where Self.definitiveFailures.contains(error.code) {
            listing = FsDirsResponse(path: request.dir, dirs: [], truncated: false)
        } catch {
            return false
        }
        caches[key, default: DirListingCache(now: now)].store(request, listing: listing)
        return true
    }
}
