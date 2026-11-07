package com.mobaide.app.ui.chat.markdown

import androidx.compose.runtime.Immutable

@Immutable
sealed interface MdInline {
    data class Text(val text: String) : MdInline
    data class Code(val text: String) : MdInline
    data class Bold(val children: List<MdInline>) : MdInline
    data class Italic(val children: List<MdInline>) : MdInline
    data class Strike(val children: List<MdInline>) : MdInline
    data class Link(val url: String, val children: List<MdInline>) : MdInline
    data object LineBreak : MdInline
}

enum class MdAlign { Start, Center, End }

@Immutable
sealed interface MdBlock {
    data class Paragraph(val inlines: List<MdInline>) : MdBlock
    data class Heading(val level: Int, val inlines: List<MdInline>) : MdBlock
    data class Code(val language: String?, val code: String) : MdBlock
    data class Quote(val blocks: List<MdBlock>) : MdBlock
    data class ListBlock(val ordered: Boolean, val start: Int, val items: List<MdListItem>) : MdBlock
    data class Table(val header: List<List<MdInline>>, val aligns: List<MdAlign>, val rows: List<List<List<MdInline>>>) : MdBlock
    data object Rule : MdBlock
}

@Immutable
data class MdListItem(val checked: Boolean?, val blocks: List<MdBlock>)

/**
 * A small Markdown reader for agent output. Never interprets HTML (it stays text),
 * makes only http/https/mailto links tappable and never loads images: `![alt](url)`
 * becomes a link labelled with its alt text. An unclosed code fence runs to the end,
 * which keeps a streaming reply readable.
 */
object Markdown {

    fun parse(text: String): List<MdBlock> {
        val lines = text.replace("\r\n", "\n").replace('\r', '\n').split('\n')
        return BlockParser(lines).parse()
    }

    fun isSafeUrl(url: String): Boolean {
        val lower = url.trim().lowercase()
        return lower.startsWith("http://") || lower.startsWith("https://") || lower.startsWith("mailto:")
    }

    fun parseInline(text: String): List<MdInline> = InlineParser(text).parse()

    /** Plain text of [inlines], for copy and accessibility. */
    fun plain(inlines: List<MdInline>): String = buildString { appendPlain(inlines) }

    private fun StringBuilder.appendPlain(inlines: List<MdInline>) {
        inlines.forEach { inline ->
            when (inline) {
                is MdInline.Text -> append(inline.text)
                is MdInline.Code -> append(inline.text)
                is MdInline.Bold -> appendPlain(inline.children)
                is MdInline.Italic -> appendPlain(inline.children)
                is MdInline.Strike -> appendPlain(inline.children)
                is MdInline.Link -> appendPlain(inline.children)
                MdInline.LineBreak -> append('\n')
            }
        }
    }
}

private val FENCE = Regex("^ {0,3}(`{3,}|~{3,})\\s*([^`\\s]*)?.*$")
private val HEADING = Regex("^ {0,3}(#{1,6})(?:[ \\t]+(.*?))?(?:[ \\t]+#+)?[ \\t]*$")
private val RULE = Regex("^ {0,3}([-*_])(?:[ \\t]*\\1){2,}[ \\t]*$")
private val QUOTE = Regex("^ {0,3}> ?(.*)$")
private val LIST_ITEM = Regex("^([ \\t]*)([-*+]|\\d{1,9}[.)])([ \\t]+(.*))?$")
private val TABLE_SEPARATOR = Regex("^\\s*\\|?\\s*:?-+:?\\s*(\\|\\s*:?-+:?\\s*)*\\|?\\s*$")
private val TASK = Regex("^\\[([ xX])](?:[ \\t]+(.*))?$", RegexOption.DOT_MATCHES_ALL)

private fun indentOf(line: String): Int {
    var width = 0
    for (c in line) {
        when (c) {
            ' ' -> width += 1
            '\t' -> width += 4 - (width % 4)
            else -> return width
        }
    }
    return width
}

/** [line] with up to [count] columns of leading indentation removed. */
private fun dropIndent(line: String, count: Int): String {
    var width = 0
    var i = 0
    while (i < line.length && width < count) {
        when (line[i]) {
            ' ' -> width += 1
            '\t' -> width += 4 - (width % 4)
            else -> return line.substring(i)
        }
        i++
    }
    return line.substring(i)
}

private class BlockParser(private val lines: List<String>) {
    private var pos = 0

    fun parse(): List<MdBlock> {
        val blocks = ArrayList<MdBlock>()
        while (pos < lines.size) {
            val line = lines[pos]
            if (line.isBlank()) {
                pos++
                continue
            }
            blocks += block(line)
        }
        return blocks
    }

    private fun block(line: String): MdBlock {
        FENCE.matchEntire(line)?.let { return fence(it) }
        HEADING.matchEntire(line)?.let { match ->
            pos++
            return MdBlock.Heading(match.groupValues[1].length, Markdown.parseInline(match.groupValues[2].trim()))
        }
        if (RULE.matches(line)) {
            pos++
            return MdBlock.Rule
        }
        if (QUOTE.matches(line)) return quote()
        if (LIST_ITEM.matches(line)) return list(indentOf(line))
        if (isTableStart(pos)) return table()
        return paragraph()
    }

    private fun fence(match: MatchResult): MdBlock {
        val marker = match.groupValues[1]
        val language = match.groupValues[2].takeIf { it.isNotBlank() }
        val openIndent = indentOf(lines[pos])
        pos++
        val body = ArrayList<String>()
        while (pos < lines.size) {
            val line = lines[pos]
            val trimmed = line.trimStart()
            if (indentOf(line) <= 3 && trimmed.startsWith(marker[0].toString().repeat(marker.length)) &&
                trimmed.trimEnd().all { it == marker[0] }
            ) {
                pos++
                break
            }
            body += dropIndent(line, openIndent)
            pos++
        }
        return MdBlock.Code(language, body.joinToString("\n"))
    }

    private fun quote(): MdBlock {
        val inner = ArrayList<String>()
        while (pos < lines.size) {
            val match = QUOTE.matchEntire(lines[pos]) ?: break
            inner += match.groupValues[1]
            pos++
        }
        return MdBlock.Quote(BlockParser(inner).parse())
    }

    private fun startsBlock(line: String): Boolean =
        FENCE.matches(line) || HEADING.matches(line) || RULE.matches(line) || QUOTE.matches(line) ||
            LIST_ITEM.matches(line)

    private fun paragraph(): MdBlock {
        val text = StringBuilder()
        while (pos < lines.size) {
            val line = lines[pos]
            if (line.isBlank()) break
            if (text.isNotEmpty() && (startsBlock(line) || isTableStart(pos))) break
            if (text.isNotEmpty()) text.append('\n')
            text.append(line.trim())
            pos++
        }
        return MdBlock.Paragraph(Markdown.parseInline(text.toString()))
    }

    private fun list(baseIndent: Int): MdBlock {
        val first = LIST_ITEM.matchEntire(lines[pos])!!
        val ordered = first.groupValues[2].first().isDigit()
        val start = if (ordered) first.groupValues[2].dropLast(1).toIntOrNull() ?: 1 else 1
        val items = ArrayList<MdListItem>()
        while (pos < lines.size) {
            val match = LIST_ITEM.matchEntire(lines[pos]) ?: break
            val indent = indentOf(lines[pos])
            if (indent < baseIndent || indent >= baseIndent + 2) break
            if (match.groupValues[2].first().isDigit() != ordered) break
            items += item(match, indent)
        }
        return MdBlock.ListBlock(ordered, start, items)
    }

    private fun item(match: MatchResult, indent: Int): MdListItem {
        val marker = match.groupValues[2]
        val firstText = match.groupValues[4]
        val contentIndent = indent + marker.length + 1
        val body = arrayListOf(firstText)
        pos++
        var sawBlank = false
        while (pos < lines.size) {
            val line = lines[pos]
            if (line.isBlank()) {
                sawBlank = true
                body += ""
                pos++
                continue
            }
            val lineIndent = indentOf(line)
            if (lineIndent >= indent + 2) {
                body += dropIndent(line, minOf(lineIndent, contentIndent))
                sawBlank = false
                pos++
                continue
            }
            if (sawBlank || LIST_ITEM.matches(line) || startsBlock(line)) break
            body += line.trim()
            pos++
        }
        while (body.isNotEmpty() && body.last().isBlank()) body.removeAt(body.lastIndex)
        var checked: Boolean? = null
        TASK.matchEntire(body.firstOrNull().orEmpty())?.let { task ->
            checked = task.groupValues[1] != " "
            body[0] = task.groupValues[2]
        }
        return MdListItem(checked, BlockParser(body).parse())
    }

    private fun isTableStart(at: Int): Boolean {
        if (at + 1 >= lines.size) return false
        val header = lines[at]
        if (!header.contains('|')) return false
        val separator = lines[at + 1]
        if (!separator.contains('-') || !TABLE_SEPARATOR.matches(separator)) return false
        return splitRow(header).size == splitRow(separator).size
    }

    private fun table(): MdBlock {
        val header = splitRow(lines[pos])
        val aligns = splitRow(lines[pos + 1]).map { cell ->
            val c = cell.trim()
            when {
                c.startsWith(':') && c.endsWith(':') -> MdAlign.Center
                c.endsWith(':') -> MdAlign.End
                else -> MdAlign.Start
            }
        }
        pos += 2
        val rows = ArrayList<List<List<MdInline>>>()
        while (pos < lines.size && lines[pos].isNotBlank() && lines[pos].contains('|')) {
            val cells = splitRow(lines[pos])
            rows += List(header.size) { i -> Markdown.parseInline(cells.getOrNull(i).orEmpty().trim()) }
            pos++
        }
        return MdBlock.Table(header.map { Markdown.parseInline(it.trim()) }, aligns, rows)
    }

    private fun splitRow(line: String): List<String> {
        var row = line.trim()
        if (row.startsWith('|')) row = row.substring(1)
        if (row.endsWith('|') && !row.endsWith("\\|")) row = row.dropLast(1)
        val cells = ArrayList<String>()
        val current = StringBuilder()
        var inCode = false
        var i = 0
        while (i < row.length) {
            val c = row[i]
            when {
                c == '\\' && i + 1 < row.length && row[i + 1] == '|' -> {
                    current.append('|')
                    i++
                }
                c == '`' -> {
                    inCode = !inCode
                    current.append(c)
                }
                c == '|' && !inCode -> {
                    cells += current.toString()
                    current.clear()
                }
                else -> current.append(c)
            }
            i++
        }
        cells += current.toString()
        return cells
    }
}

private const val ESCAPABLE = "\\`*_{}[]()#+-.!|~<>\"'"

/** CommonMark caps a link label at 999 characters; it also bounds the bracket scan. */
private const val MAX_LINK_LABEL = 999

private class InlineParser(private val src: String) {

    fun parse(): List<MdInline> = parseRange(0, src.length)

    private fun parseRange(from: Int, to: Int, allowLinks: Boolean = true): List<MdInline> {
        val out = ArrayList<MdInline>()
        val text = StringBuilder()
        fun flush() {
            if (text.isNotEmpty()) {
                out += MdInline.Text(text.toString())
                text.clear()
            }
        }
        // A delimiter with no closer from some start has none from any later start
        // either, so remembering the first miss keeps unmatched openers linear.
        val noCloserFrom = HashMap<String, Int>()
        fun closer(start: Int, delimiter: String): Int {
            val missedAt = noCloserFrom[delimiter]
            if (missedAt != null && start >= missedAt) return -1
            val close = findCloser(start, to, delimiter)
            if (close < 0) noCloserFrom[delimiter] = start
            return close
        }

        // findCloser walks through the links this loop jumps over; a backtick or a
        // backslash inside one can shift that walk, so earlier misses stop holding.
        fun jumpedOver(start: Int, end: Int) {
            for (k in start until end) {
                if (src[k] == '`' || src[k] == '\\') {
                    noCloserFrom.clear()
                    return
                }
            }
        }

        var i = from
        while (i < to) {
            val c = src[i]
            when {
                c == '\\' && i + 1 < to && src[i + 1] in ESCAPABLE -> {
                    text.append(src[i + 1])
                    i += 2
                }
                c == '\\' && i + 1 < to && src[i + 1] == '\n' -> {
                    flush()
                    out += MdInline.LineBreak
                    i += 2
                }
                c == '\n' -> {
                    flush()
                    out += MdInline.LineBreak
                    i++
                }
                c == '`' -> {
                    val run = runLength(i, to, '`')
                    val close = findBacktickRun(i + run, to, run)
                    if (close < 0) {
                        text.append(src, i, i + run)
                        i += run
                    } else {
                        flush()
                        var code = src.substring(i + run, close).replace('\n', ' ')
                        if (code.length > 2 && code.startsWith(' ') && code.endsWith(' ') && code.isNotBlank()) {
                            code = code.substring(1, code.length - 1)
                        }
                        out += MdInline.Code(code)
                        i = close + run
                    }
                }
                allowLinks && c == '!' && i + 1 < to && src[i + 1] == '[' -> {
                    val link = link(i + 1, to)
                    if (link == null) {
                        text.append(c)
                        i++
                    } else {
                        flush()
                        val label = src.substring(link.labelStart, link.labelEnd).ifBlank { link.url }
                        out += if (Markdown.isSafeUrl(link.url)) {
                            MdInline.Link(link.url, listOf(MdInline.Text(label)))
                        } else {
                            MdInline.Text(label)
                        }
                        jumpedOver(i, link.end)
                        i = link.end
                    }
                }
                allowLinks && c == '[' -> {
                    val link = link(i, to)
                    if (link == null) {
                        text.append(c)
                        i++
                    } else {
                        flush()
                        // CommonMark forbids links inside a link label.
                        val children = parseRange(link.labelStart, link.labelEnd, allowLinks = false)
                        if (Markdown.isSafeUrl(link.url)) out += MdInline.Link(link.url, children) else out += children
                        jumpedOver(i, link.end)
                        i = link.end
                    }
                }
                (c == '*' || c == '_') && i + 1 < to && src[i + 1] == c -> {
                    val close = if (canOpen(i, 2, c)) closer(i + 2, if (c == '*') "**" else "__") else -1
                    if (close < 0) {
                        text.append(c).append(c)
                        i += 2
                    } else {
                        flush()
                        out += MdInline.Bold(parseRange(i + 2, close, allowLinks))
                        i = close + 2
                    }
                }
                c == '*' || c == '_' -> {
                    val close = if (canOpen(i, 1, c)) closer(i + 1, if (c == '*') "*" else "_") else -1
                    if (close < 0) {
                        text.append(c)
                        i++
                    } else {
                        flush()
                        out += MdInline.Italic(parseRange(i + 1, close, allowLinks))
                        i = close + 1
                    }
                }
                c == '~' && i + 1 < to && src[i + 1] == '~' -> {
                    val close = if (canOpen(i, 2, c)) closer(i + 2, "~~") else -1
                    if (close < 0) {
                        text.append("~~")
                        i += 2
                    } else {
                        flush()
                        out += MdInline.Strike(parseRange(i + 2, close, allowLinks))
                        i = close + 2
                    }
                }
                allowLinks && c == '<' && isAutolinkStart(i + 1, to) && bracketAutolinkEnd(i + 1, to) > 0 -> {
                    val close = bracketAutolinkEnd(i + 1, to)
                    flush()
                    val url = src.substring(i + 1, close)
                    out += MdInline.Link(url, listOf(MdInline.Text(url)))
                    jumpedOver(i, close + 1)
                    i = close + 1
                }
                allowLinks && (c == 'h' || c == 'H') && isAutolinkStart(i, to) -> {
                    val end = autolinkEnd(i, to)
                    flush()
                    val url = src.substring(i, end)
                    out += MdInline.Link(url, listOf(MdInline.Text(url)))
                    jumpedOver(i, end)
                    i = end
                }
                else -> {
                    text.append(c)
                    i++
                }
            }
        }
        flush()
        return out
    }

    private fun runLength(at: Int, to: Int, c: Char): Int {
        var n = 0
        while (at + n < to && src[at + n] == c) n++
        return n
    }

    private fun findBacktickRun(from: Int, to: Int, length: Int): Int {
        var i = from
        while (i < to) {
            if (src[i] == '`') {
                val run = runLength(i, to, '`')
                if (run == length) return i
                i += run
            } else {
                i++
            }
        }
        return -1
    }

    /** An opener must be followed by non-space, and `_` must not sit inside a word. */
    private fun canOpen(at: Int, length: Int, c: Char): Boolean {
        val next = src.getOrNull(at + length) ?: return false
        if (next.isWhitespace()) return false
        if (c == '_' && at > 0 && src[at - 1].isLetterOrDigit()) return false
        return true
    }

    private fun findCloser(from: Int, to: Int, delimiter: String): Int {
        val c = delimiter[0]
        var i = from
        while (i < to) {
            val ch = src[i]
            when {
                ch == '\\' -> i += 2
                ch == '`' -> {
                    val run = runLength(i, to, '`')
                    val close = findBacktickRun(i + run, to, run)
                    i = if (close < 0) i + run else close + run
                }
                ch == c -> {
                    val run = runLength(i, to, c)
                    val fits = run == delimiter.length || (delimiter.length == 2 && run == 3)
                    if (fits && i > from && !src[i - 1].isWhitespace()) {
                        val after = src.getOrNull(i + delimiter.length)
                        if (c != '_' || after == null || !after.isLetterOrDigit()) {
                            return if (delimiter.length == 2 && run == 3) i + 1 else i
                        }
                    }
                    i += run
                }
                else -> i++
            }
        }
        return -1
    }

    private class LinkMatch(val labelStart: Int, val labelEnd: Int, val url: String, val end: Int)

    private fun link(open: Int, to: Int): LinkMatch? {
        var depth = 0
        var i = open
        var labelEnd = -1
        val labelLimit = minOf(to, open + MAX_LINK_LABEL + 2)
        while (i < labelLimit) {
            when (src[i]) {
                '\\' -> i++
                '[' -> depth++
                ']' -> {
                    depth--
                    if (depth == 0) {
                        labelEnd = i
                        break
                    }
                }
                '\n' -> if (i > open && src.getOrNull(i - 1) == '\n') return null
            }
            i++
        }
        if (labelEnd < 0 || labelEnd + 1 >= to || src[labelEnd + 1] != '(') return null
        var j = labelEnd + 2
        var parens = 1
        val urlStart = j
        while (j < to) {
            when (src[j]) {
                '(' -> parens++
                ')' -> {
                    parens--
                    if (parens == 0) break
                }
                '\n' -> return null
            }
            j++
        }
        if (j >= to) return null
        var target = src.substring(urlStart, j).trim()
        val titleAt = target.indexOfFirst { it == ' ' }
        if (titleAt > 0) target = target.substring(0, titleAt)
        target = target.removePrefix("<").removeSuffix(">")
        if (target.isEmpty()) return null
        return LinkMatch(labelStart = open + 1, labelEnd = labelEnd, url = target, end = j + 1)
    }

    private fun isAutolinkStart(at: Int, to: Int): Boolean {
        if (at > 0 && (src[at - 1].isLetterOrDigit() || src[at - 1] == '/')) return false
        val rest = src.regionMatches(at, "https://", 0, 8, ignoreCase = true) ||
            src.regionMatches(at, "http://", 0, 7, ignoreCase = true)
        if (!rest) return false
        val schemeEnd = if (src.regionMatches(at, "https://", 0, 8, ignoreCase = true)) at + 8 else at + 7
        return schemeEnd < to && !src[schemeEnd].isWhitespace()
    }

    /** The index of the `>` closing a `<https://…>` autolink that starts at [at], or -1. */
    private fun bracketAutolinkEnd(at: Int, to: Int): Int {
        var end = at
        while (end < to && !src[end].isWhitespace() && src[end] != '<') {
            if (src[end] == '>') return if (end > at) end else -1
            end++
        }
        return -1
    }

    private fun autolinkEnd(at: Int, to: Int): Int {
        var end = at
        while (end < to && !src[end].isWhitespace() && src[end] != '<' && src[end] != '>' && src[end] != '`') end++
        while (end > at) {
            val last = src[end - 1]
            if (last in ".,;:!?*_~'\"") {
                end--
            } else if (last == ')') {
                val opens = src.substring(at, end).count { it == '(' }
                val closes = src.substring(at, end).count { it == ')' }
                if (closes > opens) end-- else break
            } else {
                break
            }
        }
        return end
    }
}
