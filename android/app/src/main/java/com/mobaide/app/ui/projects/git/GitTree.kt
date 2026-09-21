package com.mobaide.app.ui.projects.git

import com.mobaide.app.data.network.dto.GitItem

/**
 * Branch-list order on all three clients: lowercased names by UTF-16 code unit
 * (`String.compareTo`, not a locale `Collator`, whose order the web and iOS do
 * not share), with the raw name as tiebreak.
 */
internal fun compareRefNames(a: String, b: String): Int {
    val byLower = a.lowercase().compareTo(b.lowercase())
    return if (byLower != 0) byLower else a.compareTo(b)
}

internal val RefNameOrder: Comparator<String> = Comparator(::compareRefNames)

enum class GitTreeScope { LOCAL, REMOTE }

/** One visible line of a section's folder tree. */
sealed interface GitTreeRow {
    val key: String
    val depth: Int

    /** [count] is the rows below it that the current filter shows. */
    data class Folder(
        val id: String,
        val name: String,
        override val depth: Int,
        val count: Int,
        val expanded: Boolean,
    ) : GitTreeRow {
        override val key: String get() = id
    }

    /** [label] is the last path segment; dialogs and accessibility keep [primaryLabel]. */
    data class Leaf(
        val item: GitItem,
        override val depth: Int,
        val label: String,
    ) : GitTreeRow {
        override val key: String get() = item.id
    }
}

/**
 * Where a row sits in the tree: its branch split on `/`, a remote-tracking row
 * under a top folder named by its `remote` (which may itself contain `/`).
 */
internal fun treeSegments(item: GitItem): List<String> {
    val branch = item.branch.orEmpty()
    val remote = item.remote.orEmpty()
    if (item.kind == GitItem.KIND_REMOTE_BRANCH && remote.isNotEmpty() && branch.startsWith("$remote/")) {
        return listOf(remote) + splitPath(branch.substring(remote.length + 1))
    }
    return splitPath(branch)
}

private fun splitPath(name: String): List<String> = name.split('/').filter { it.isNotEmpty() }

/**
 * `:` is illegal in ref and remote names, so it separates a remote from the
 * path below it: remotes `a` and `a/b` never share a folder id, and no folder
 * id can equal a row id (`<kind>:…`).
 */
internal fun folderId(scope: GitTreeScope, segments: List<String>): String = when (scope) {
    GitTreeScope.REMOTE ->
        "dir:remote:${segments.firstOrNull().orEmpty()}:${segments.drop(1).joinToString("/")}"
    GitTreeScope.LOCAL -> "dir:local:${segments.joinToString("/")}"
}

/** Remote-name folders start open — the collapsed section is that level already. */
private fun expandedByDefault(scope: GitTreeScope, depth: Int): Boolean =
    scope == GitTreeScope.REMOTE && depth == 0

private class TreeNode(val segments: List<String>) {
    val folders = mutableMapOf<String, TreeNode>()
    val leaves = mutableListOf<GitTreeRow.Leaf>()

    fun leafCount(): Int = leaves.size + folders.values.sumOf { it.leafCount() }
}

/**
 * [toggled] holds the folders the user flipped away from their default; an
 * active filter opens every folder (the rows passed in are already filtered,
 * so a folder without a match never appears). Orphan worktrees have no
 * branch to file them under and follow the local tree by path.
 */
internal fun gitTreeRows(
    items: List<GitItem>,
    scope: GitTreeScope,
    toggled: Set<String>,
    filterActive: Boolean,
): List<GitTreeRow> {
    val root = TreeNode(emptyList())
    val orphans = mutableListOf<GitItem>()
    for (item in items) {
        if (item.kind == GitItem.KIND_ORPHAN_WORKTREE) {
            orphans += item
            continue
        }
        val segments = treeSegments(item)
        var node = root
        for (segment in segments.dropLast(1)) {
            node = node.folders.getOrPut(segment) { TreeNode(node.segments + segment) }
        }
        node.leaves += GitTreeRow.Leaf(
            item = item,
            depth = node.segments.size,
            label = segments.lastOrNull() ?: primaryLabel(item),
        )
    }

    val rows = mutableListOf<GitTreeRow>()
    fun visit(node: TreeNode) {
        val depth = node.segments.size
        for (name in node.folders.keys.sortedWith(RefNameOrder)) {
            val folder = node.folders.getValue(name)
            val id = folderId(scope, folder.segments)
            val expanded = filterActive || (expandedByDefault(scope, depth) != (id in toggled))
            rows += GitTreeRow.Folder(id, name, depth, folder.leafCount(), expanded)
            if (expanded) visit(folder)
        }
        node.leaves.sortedWith(compareBy(RefNameOrder) { it.label }).forEach { rows += it }
    }
    visit(root)

    orphans.sortedWith(compareBy(RefNameOrder) { primaryLabel(it) })
        .forEach { rows += GitTreeRow.Leaf(it, depth = 0, label = primaryLabel(it)) }
    return rows
}
