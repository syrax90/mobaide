package com.mobaide.app.data.fs

import com.mobaide.app.data.network.dto.FsDirsResponse

/**
 * Folder suggestions for host-path fields, over `GET /api/fs/dirs`.
 *
 * A directory is listed once and keystrokes inside it filter that listing locally; the
 * fragment goes up as `q` only for a truncated one. Port of web `lib/pathCompletion.ts`
 * and iOS `PathCompletion.swift`.
 */
object PathCompletion {
    const val MAX_VISIBLE_SUGGESTIONS = 8
    const val DEBOUNCE_MS = 200L
    const val LISTING_TTL_MS = 30_000L

    private val WINDOWS_DRIVE_ROOT = Regex("^[A-Za-z]:[\\\\/]")
    private val WINDOWS_DRIVE_ROOT_ONLY = Regex("^[A-Za-z]:[\\\\/]$")

    /** [dir] is what gets listed (verbatim, separator included); [fragment] filters it. */
    data class Query(val dir: String, val fragment: String)

    /**
     * `null` when the text cannot name an absolute directory yet: empty,
     * relative, a bare drive letter, or `~user` without a separator.
     */
    fun split(text: String): Query? {
        if (text == "~") return Query("~/", "")
        if (!(text.startsWith("/") || text.startsWith("~") || WINDOWS_DRIVE_ROOT.containsMatchIn(text))) {
            return null
        }
        val lastSep = maxOf(text.lastIndexOf('/'), text.lastIndexOf('\\'))
        if (lastSep < 0) return null
        return Query(text.substring(0, lastSep + 1), text.substring(lastSep + 1))
    }

    /**
     * Case-insensitive matches: prefix matches first, then names that merely
     * contain the fragment, each group in the agent's order. Dot directories
     * stay hidden until the fragment itself starts with a dot.
     */
    fun rank(dirs: List<String>, fragment: String): List<String> {
        val needle = fragment.lowercase()
        val showHidden = fragment.startsWith(".")
        val prefix = mutableListOf<String>()
        val contains = mutableListOf<String>()
        for (name in dirs) {
            if (!showHidden && name.startsWith(".")) continue
            val lower = name.lowercase()
            if (lower.startsWith(needle)) prefix += name
            else if (lower.contains(needle)) contains += name
        }
        return prefix + contains
    }

    /** The separator a listing of [listedPath] is extended with: `\` only for an all-backslash path. */
    fun separator(listedPath: String): String =
        if (listedPath.contains('\\') && !listedPath.contains('/')) "\\" else "/"

    /** The field's new text after picking [name] from a listing of [listedPath]. */
    fun accept(listedPath: String, name: String): String {
        val sep = separator(listedPath)
        val base = if (listedPath.endsWith('/') || listedPath.endsWith('\\')) listedPath else listedPath + sep
        return base + name + sep
    }

    /** Drops the trailing separator a picked suggestion leaves behind; roots keep theirs. */
    fun trimTrailingSeparator(path: String): String {
        if (path.length <= 1 || WINDOWS_DRIVE_ROOT_ONLY.matches(path)) return path
        return if (path.endsWith('/') || path.endsWith('\\')) path.dropLast(1) else path
    }
}

/** What to ask the agent for: [q] is null for an unfiltered listing. */
data class ListingRequest(val dir: String, val q: String?)

/** A cached listing; [complete] is false for a truncated one, shown but re-asked with `q`. */
data class ListingHit(val listing: FsDirsResponse, val complete: Boolean)

/**
 * LRU of listings keyed by `(dir, q)`. A listing answers a lookup locally when
 * it is untruncated and either unfiltered or filtered by a `q` the fragment
 * contains — every name containing the fragment then contains `q` too. Not
 * thread-safe: callers confine it to one thread.
 */
class DirListingCache(private val now: () -> Long = System::currentTimeMillis) {
    private data class Entry(val listing: FsDirsResponse, val q: String?, val storedAt: Long)

    private val entries = object : LinkedHashMap<Pair<String, String?>, Entry>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<Pair<String, String?>, Entry>?) =
            size > MAX_ENTRIES
    }

    fun lookup(dir: String, fragment: String): ListingHit? {
        val needle = fragment.lowercase()
        var partial: FsDirsResponse? = null
        val iterator = entries.entries.iterator()
        var completeKey: Pair<String, String?>? = null
        while (iterator.hasNext()) {
            val (key, entry) = iterator.next()
            if (key.first != dir) continue
            if (now() - entry.storedAt > PathCompletion.LISTING_TTL_MS) {
                iterator.remove()
                continue
            }
            val covers = entry.q == null || needle.contains(entry.q.lowercase())
            if (!covers) continue
            if (!entry.listing.truncated) {
                completeKey = key
                break
            }
            if (partial == null) partial = entry.listing
        }
        // Read through the access-ordered map only after iterating, so the
        // LRU touch cannot invalidate the iterator.
        completeKey?.let { key -> entries[key]?.let { return ListingHit(it.listing, complete = true) } }
        return partial?.let { ListingHit(it, complete = false) }
    }

    fun hasUnfiltered(dir: String): Boolean {
        val entry = entries[dir to null] ?: return false
        return now() - entry.storedAt <= PathCompletion.LISTING_TTL_MS
    }

    fun store(request: ListingRequest, listing: FsDirsResponse) {
        entries[request.dir to request.q] = Entry(listing, request.q, now())
    }

    /**
     * What to ask the agent for next, or `null` when the cache already answers.
     * A directory never listed is fetched unfiltered first; only a truncated one
     * is re-asked with the fragment as `q`.
     */
    fun nextRequest(query: PathCompletion.Query): ListingRequest? {
        if (lookup(query.dir, query.fragment)?.complete == true) return null
        if (!hasUnfiltered(query.dir)) return ListingRequest(query.dir, null)
        if (query.fragment.isEmpty()) return null
        return ListingRequest(query.dir, query.fragment)
    }

    private companion object {
        const val MAX_ENTRIES = 32
    }
}
