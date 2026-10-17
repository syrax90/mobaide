import Foundation

/// Folder suggestions for host-path fields, over `GET /api/fs/dirs`.
///
/// A directory is listed once and keystrokes inside it filter that listing
/// locally; the fragment goes up as `q` only for a truncated one. Port of web
/// `lib/pathCompletion.ts` and Android `PathCompletion.kt`.
enum PathCompletion {
    static let maxVisibleSuggestions = 8
    static let debounce: Duration = .milliseconds(200)
    static let listingTTL: Duration = .seconds(30)

    /// `dir` is what gets listed (verbatim, separator included); `fragment` filters it.
    struct Query: Equatable, Sendable {
        let dir: String
        let fragment: String
    }

    /// `nil` when the text cannot name an absolute directory yet: empty,
    /// relative, a bare drive letter, or `~user` without a separator.
    static func split(_ text: String) -> Query? {
        if text == "~" { return Query(dir: "~/", fragment: "") }
        guard text.hasPrefix("/") || text.hasPrefix("~") || hasWindowsDriveRoot(text) else { return nil }
        guard let lastSep = text.lastIndex(where: { $0 == "/" || $0 == "\\" }) else { return nil }
        let afterSep = text.index(after: lastSep)
        return Query(dir: String(text[..<afterSep]), fragment: String(text[afterSep...]))
    }

    /// Case-insensitive matches: prefix matches first, then names that merely
    /// contain the fragment, each group in the agent's order. Dot directories
    /// stay hidden until the fragment itself starts with a dot.
    static func rank(_ dirs: [String], fragment: String) -> [String] {
        let needle = fragment.lowercased()
        let showHidden = fragment.hasPrefix(".")
        var prefix: [String] = []
        var contains: [String] = []
        for name in dirs {
            if !showHidden, name.hasPrefix(".") { continue }
            let lower = name.lowercased()
            if lower.hasPrefix(needle) {
                prefix.append(name)
            } else if lower.contains(needle) {
                contains.append(name)
            }
        }
        return prefix + contains
    }

    /// The separator a listing of `listedPath` is extended with: `\` only for an all-backslash path.
    static func separator(listedPath: String) -> String {
        listedPath.contains("\\") && !listedPath.contains("/") ? "\\" : "/"
    }

    /// The field's new text after picking `name` from a listing of `listedPath`.
    static func accept(listedPath: String, name: String) -> String {
        let sep = separator(listedPath: listedPath)
        let base = listedPath.hasSuffix("/") || listedPath.hasSuffix("\\") ? listedPath : listedPath + sep
        return base + name + sep
    }

    /// Drops the trailing separator a picked suggestion leaves behind; roots keep theirs.
    static func trimTrailingSeparator(_ path: String) -> String {
        guard path.count > 1, !isWindowsDriveRootOnly(path) else { return path }
        return path.hasSuffix("/") || path.hasSuffix("\\") ? String(path.dropLast()) : path
    }

    private static func hasWindowsDriveRoot(_ text: String) -> Bool {
        let chars = Array(text.prefix(3))
        return chars.count == 3 && chars[0].isASCII && chars[0].isLetter && chars[1] == ":"
            && (chars[2] == "/" || chars[2] == "\\")
    }

    private static func isWindowsDriveRootOnly(_ text: String) -> Bool {
        text.count == 3 && hasWindowsDriveRoot(text)
    }
}

/// What to ask the agent for: `q` is `nil` for an unfiltered listing.
struct ListingRequest: Equatable, Sendable {
    let dir: String
    let q: String?
}

/// A cached listing; `complete` is false for a truncated one, shown but re-asked with `q`.
struct ListingHit: Equatable, Sendable {
    let listing: FsDirsResponse
    let complete: Bool
}

/// LRU of listings keyed by `(dir, q)`. A listing answers a lookup locally
/// when it is untruncated and either unfiltered or filtered by a `q` the
/// fragment contains — every name containing the fragment then contains `q`.
struct DirListingCache {
    private struct Key: Hashable {
        let dir: String
        let q: String?
    }

    private struct Entry {
        let listing: FsDirsResponse
        let storedAt: ContinuousClock.Instant
    }

    private static let maxEntries = 32

    private var entries: [Key: Entry] = [:]
    /// Least recently used first.
    private var order: [Key] = []
    private let now: () -> ContinuousClock.Instant

    init(now: @escaping () -> ContinuousClock.Instant = { ContinuousClock.now }) {
        self.now = now
    }

    mutating func lookup(dir: String, fragment: String) -> ListingHit? {
        let needle = fragment.lowercased()
        var partial: FsDirsResponse?
        for key in order where key.dir == dir {
            guard let entry = entries[key] else { continue }
            if now() - entry.storedAt > PathCompletion.listingTTL {
                remove(key)
                continue
            }
            let covers = key.q.map { needle.contains($0.lowercased()) } ?? true
            guard covers else { continue }
            if !entry.listing.truncated {
                touch(key)
                return ListingHit(listing: entry.listing, complete: true)
            }
            if partial == nil { partial = entry.listing }
        }
        return partial.map { ListingHit(listing: $0, complete: false) }
    }

    func hasUnfiltered(dir: String) -> Bool {
        guard let entry = entries[Key(dir: dir, q: nil)] else { return false }
        return now() - entry.storedAt <= PathCompletion.listingTTL
    }

    mutating func store(_ request: ListingRequest, listing: FsDirsResponse) {
        let key = Key(dir: request.dir, q: request.q)
        entries[key] = Entry(listing: listing, storedAt: now())
        touch(key)
        while order.count > Self.maxEntries {
            entries[order.removeFirst()] = nil
        }
    }

    /// What to ask the agent for next, or `nil` when the cache already
    /// answers. A directory never listed is fetched unfiltered first; only a
    /// truncated one is re-asked with the fragment as `q`.
    mutating func nextRequest(_ query: PathCompletion.Query) -> ListingRequest? {
        if lookup(dir: query.dir, fragment: query.fragment)?.complete == true { return nil }
        if !hasUnfiltered(dir: query.dir) { return ListingRequest(dir: query.dir, q: nil) }
        if query.fragment.isEmpty { return nil }
        return ListingRequest(dir: query.dir, q: query.fragment)
    }

    private mutating func touch(_ key: Key) {
        order.removeAll { $0 == key }
        order.append(key)
    }

    private mutating func remove(_ key: Key) {
        entries[key] = nil
        order.removeAll { $0 == key }
    }
}
