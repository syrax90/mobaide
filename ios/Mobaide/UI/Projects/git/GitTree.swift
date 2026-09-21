import Foundation

/// Branch-list order on all three clients: lowercased names by UTF-16 code
/// unit — not Swift's `<` or `==`, which treat canonically equivalent spellings
/// as one string, nor a localized compare, whose collation Android and the web
/// do not share — with the raw name as tiebreak.
enum GitRefOrder {
    static func precedes(_ lhs: String, _ rhs: String) -> Bool {
        let lower = (lhs.lowercased().utf16, rhs.lowercased().utf16)
        if !lower.0.elementsEqual(lower.1) {
            return lower.0.lexicographicallyPrecedes(lower.1)
        }
        return lhs.utf16.lexicographicallyPrecedes(rhs.utf16)
    }
}

enum GitTreeScope {
    case local
    case remote
}

/// One visible line of a section's folder tree.
enum GitTreeRow: Equatable {
    /// `count` is the rows below it that the current filter shows.
    case folder(id: String, name: String, depth: Int, count: Int, expanded: Bool)
    /// `label` is the last path segment; dialogs and accessibility keep `primaryLabel`.
    case leaf(item: GitItem, depth: Int, label: String)

    var key: String {
        switch self {
        case .folder(let id, _, _, _, _): return id
        case .leaf(let item, _, _): return item.id
        }
    }

    var depth: Int {
        switch self {
        case .folder(_, _, let depth, _, _), .leaf(_, let depth, _): return depth
        }
    }
}

enum GitTree {
    /// Where a row sits in the tree: its branch split on `/`, a remote-tracking
    /// row under a top folder named by its `remote` (which may itself contain `/`).
    static func segments(_ item: GitItem) -> [String] {
        let branch = item.branch ?? ""
        let remote = item.remote ?? ""
        if item.kind == GitItem.kindRemoteBranch, !remote.isEmpty, branch.hasPrefix(remote + "/") {
            return [remote] + splitPath(String(branch.dropFirst(remote.count + 1)))
        }
        return splitPath(branch)
    }

    private static func splitPath(_ name: String) -> [String] {
        name.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
    }

    /// `:` is illegal in ref and remote names, so it separates a remote from the
    /// path below it: remotes `a` and `a/b` never share a folder id, and no
    /// folder id can equal a row id (`<kind>:…`).
    static func folderID(_ scope: GitTreeScope, _ segments: [String]) -> String {
        switch scope {
        case .remote:
            return "dir:remote:\(segments.first ?? ""):\(segments.dropFirst().joined(separator: "/"))"
        case .local:
            return "dir:local:\(segments.joined(separator: "/"))"
        }
    }

    /// Remote-name folders start open — the collapsed section is that level already.
    private static func expandedByDefault(_ scope: GitTreeScope, depth: Int) -> Bool {
        scope == .remote && depth == 0
    }

    /// Keyed by code units: a `String` key would merge canonically equivalent
    /// folder names (`é` and `e\u{301}`) the other clients keep apart.
    private final class Node {
        let segments: [String]
        var folders: [[UInt16]: Node] = [:]
        var leaves: [(item: GitItem, label: String)] = []

        init(segments: [String]) { self.segments = segments }

        var leafCount: Int { leaves.count + folders.values.reduce(0) { $0 + $1.leafCount } }
    }

    /// `toggled` holds the folders the user flipped away from their default; an
    /// active filter opens every folder (the rows passed in are already
    /// filtered, so a folder without a match never appears). Orphan worktrees
    /// have no branch to file them under and follow the local tree by path.
    static func rows(
        _ items: [GitItem],
        scope: GitTreeScope,
        toggled: Set<String>,
        filterActive: Bool
    ) -> [GitTreeRow] {
        let root = Node(segments: [])
        var orphans: [GitItem] = []
        for item in items {
            if item.kind == GitItem.kindOrphanWorktree {
                orphans.append(item)
                continue
            }
            let path = segments(item)
            var node = root
            for segment in path.dropLast() {
                let key = Array(segment.utf16)
                if let child = node.folders[key] {
                    node = child
                } else {
                    let child = Node(segments: node.segments + [segment])
                    node.folders[key] = child
                    node = child
                }
            }
            node.leaves.append((item, path.last ?? GitItemDisplay.primaryLabel(item)))
        }

        var rows: [GitTreeRow] = []
        func visit(_ node: Node) {
            let depth = node.segments.count
            let folders = node.folders.values.sorted {
                GitRefOrder.precedes($0.segments.last ?? "", $1.segments.last ?? "")
            }
            for folder in folders {
                let name = folder.segments.last ?? ""
                let id = folderID(scope, folder.segments)
                let expanded = filterActive || (expandedByDefault(scope, depth: depth) != toggled.contains(id))
                rows.append(.folder(id: id, name: name, depth: depth, count: folder.leafCount, expanded: expanded))
                if expanded { visit(folder) }
            }
            for leaf in node.leaves.sorted(by: { GitRefOrder.precedes($0.label, $1.label) }) {
                rows.append(.leaf(item: leaf.item, depth: depth, label: leaf.label))
            }
        }
        visit(root)

        for item in orphans.sorted(by: { GitRefOrder.precedes(GitItemDisplay.primaryLabel($0), GitItemDisplay.primaryLabel($1)) }) {
            rows.append(.leaf(item: item, depth: 0, label: GitItemDisplay.primaryLabel(item)))
        }
        return rows
    }
}
