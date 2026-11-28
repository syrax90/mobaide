package com.mobaide.app.data.chat

/**
 * `@` file mentions in the chat composer: which token the cursor is in, what a
 * picked suggestion turns it into, and which picked files the text still names
 * when it is sent. Port of web `chat/chatMentions.ts`; iOS `ChatMentions.swift`
 * follows the same rules.
 */
object ChatMentions {
    /** The agent takes at most this many per prompt. */
    const val MAX_MENTIONS = 10
    const val SUGGESTIONS_LIMIT = 20
    const val DEBOUNCE_MS = 150L

    private const val TRAILING_PUNCTUATION = ".,;:!?)]}'\""

    /** How far back from the cursor a picked path's `@` is looked for (the agent's path cap). */
    private const val PICKED_SCAN_MAX = 4096

    /** The `@` token around the cursor: `[start, end)`; [query] is what is typed after the `@`. */
    data class Token(val start: Int, val end: Int, val query: String)

    /**
     * The token the cursor is in when it starts with `@` at the start of the text
     * or after whitespace, with no whitespace up to the cursor; null otherwise. The
     * `@` must be before the cursor: a cursor just in front of it is not in the token.
     * A [picked] path counts as one token even with spaces in it (`@my docs/`),
     * together with what is typed right after it.
     */
    fun tokenAt(text: String, cursor: Int, picked: List<String> = emptyList()): Token? {
        val at = cursor.coerceIn(0, text.length)
        return pickedTokenAt(text, at, picked) ?: plainTokenAt(text, at)
    }

    /** The token of a picked path the cursor is in, found from the nearest `@` before the cursor. */
    private fun pickedTokenAt(text: String, at: Int, picked: List<String>): Token? {
        if (picked.isEmpty()) return null
        val floor = maxOf(0, at - PICKED_SCAN_MAX)
        var p = at - 1
        while (p >= floor && text[p] != '\n') {
            if (text[p] == '@' && (p == 0 || text[p - 1].isWhitespace())) {
                val longest = picked.filter { text.startsWith(it, p + 1) }.maxOfOrNull { it.length }
                if (longest != null) {
                    var end = p + 1 + longest
                    while (end < text.length && !text[end].isWhitespace()) end++
                    if (at <= end) return Token(p, end, text.substring(p + 1, at))
                }
            }
            p--
        }
        return null
    }

    private fun plainTokenAt(text: String, at: Int): Token? {
        var start = at
        while (start > 0 && !text[start - 1].isWhitespace()) start--
        if (start >= at || text[start] != '@') return null
        var end = at
        while (end < text.length && !text[end].isWhitespace()) end++
        return Token(start, end, text.substring(start + 1, at))
    }

    /** What the composer holds for a picked entry: folders end in `/`. */
    fun value(path: String, dir: Boolean): String = if (dir) "$path/" else path

    /**
     * [text] with [token] replaced by `@value`, and the cursor after it. A file is
     * followed by a space (unless one is already there); a folder is not, so
     * typing goes on inside it.
     */
    fun insert(text: String, token: Token, value: String): Pair<String, Int> {
        val before = text.substring(0, token.start)
        val after = text.substring(token.end)
        val inserted = "@$value"
        if (value.endsWith("/")) return (before + inserted + after) to (before.length + inserted.length)
        val space = if (after.firstOrNull()?.isWhitespace() == true) "" else " "
        return (before + inserted + space + after) to (before.length + inserted.length + 1)
    }

    /** The [picked] mentions whose token is still in [text], in pick order, at most [MAX_MENTIONS]. */
    fun inText(text: String, picked: List<String>): List<String> =
        picked.distinct().filter { names(text, it) }.take(MAX_MENTIONS)

    private fun names(text: String, value: String): Boolean {
        val token = "@$value"
        var at = text.indexOf(token)
        while (at >= 0) {
            val end = at + token.length
            val startsToken = at == 0 || text[at - 1].isWhitespace()
            val endsToken = end == text.length || text[end].isWhitespace() ||
                (text[end] in TRAILING_PUNCTUATION && (end + 1 == text.length || text[end + 1].isWhitespace()))
            if (startsToken && endsToken) return true
            at = text.indexOf(token, at + 1)
        }
        return false
    }
}
