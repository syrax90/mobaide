import Foundation

/// The block layer of the chat Markdown subset: fenced code (an unclosed fence
/// runs to the end, which matters while text streams), ATX headings, nested
/// bullet / ordered / task lists, block quotes, thematic breaks, pipe tables
/// and paragraphs. Inline spans are left as source text for the renderer.
indirect enum MarkdownBlock: Equatable, Sendable {
    case paragraph(String)
    case heading(level: Int, text: String)
    case code(language: String?, text: String)
    case list(MarkdownList)
    case quote([MarkdownBlock])
    case rule
    case table(MarkdownTable)
}

struct MarkdownList: Equatable, Sendable {
    struct Item: Equatable, Sendable {
        /// nil for a plain item; otherwise whether the task box is checked.
        let task: Bool?
        let blocks: [MarkdownBlock]
    }

    let ordered: Bool
    let start: Int
    var items: [Item]
}

struct MarkdownTable: Equatable, Sendable {
    enum Alignment: Equatable, Sendable {
        case leading, center, trailing
    }

    let header: [String]
    let alignments: [Alignment]
    let rows: [[String]]
}

enum MarkdownParser {
    static let maxDepth = 12
    /// The empty cells one table may add. Each row is padded to the header's
    /// width, so a wide header over many short rows would otherwise turn a
    /// small message into millions of cells; the rows past the limit stay text.
    static let maxPaddedCells = 10_000

    static func parse(_ text: String) -> [MarkdownBlock] {
        let lines = text.components(separatedBy: "\n").map { line -> String in
            let trimmed = line.hasSuffix("\r") ? String(line.dropLast()) : line
            return expandLeadingTabs(trimmed)
        }
        return parseBlocks(lines, depth: 0)
    }

    // MARK: - Blocks

    private static func parseBlocks(_ lines: [String], depth: Int) -> [MarkdownBlock] {
        guard depth < maxDepth else {
            let text = lines.map { $0.trimmingCharacters(in: .whitespaces) }.joined(separator: "\n")
            return text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? [] : [.paragraph(text)]
        }
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var index = 0

        func flushParagraph() {
            guard !paragraph.isEmpty else { return }
            blocks.append(.paragraph(paragraph.joined(separator: "\n")))
            paragraph = []
        }

        while index < lines.count {
            let line = lines[index]
            if isBlank(line) {
                flushParagraph()
                index += 1
                continue
            }
            if let fence = fenceOpen(line) {
                flushParagraph()
                var body: [String] = []
                index += 1
                while index < lines.count {
                    if isFenceClose(lines[index], fence: fence) {
                        index += 1
                        break
                    }
                    body.append(stripIndent(lines[index], fence.indent))
                    index += 1
                }
                blocks.append(.code(language: fence.language, text: body.joined(separator: "\n")))
                continue
            }
            if let heading = heading(line) {
                flushParagraph()
                blocks.append(.heading(level: heading.level, text: heading.text))
                index += 1
                continue
            }
            if isRule(line) {
                flushParagraph()
                blocks.append(.rule)
                index += 1
                continue
            }
            if quoteContent(line) != nil {
                flushParagraph()
                var inner: [String] = []
                while index < lines.count {
                    let current = lines[index]
                    if let content = quoteContent(current) {
                        inner.append(content)
                    } else if !isBlank(current), !startsBlock(current), let last = inner.last, !isBlank(last) {
                        inner.append(current)
                    } else {
                        break
                    }
                    index += 1
                }
                blocks.append(.quote(parseBlocks(inner, depth: depth + 1)))
                continue
            }
            if index + 1 < lines.count, line.contains("|"), let alignments = tableSeparator(lines[index + 1]) {
                let header = splitRow(line)
                if !header.isEmpty {
                    flushParagraph()
                    index += 2
                    var rows: [[String]] = []
                    var padded = 0
                    while index < lines.count, !isBlank(lines[index]), !startsBlock(lines[index]) {
                        let cells = splitRow(lines[index])
                        padded += max(0, header.count - cells.count)
                        if padded > maxPaddedCells { break }
                        rows.append(normalize(cells, count: header.count))
                        index += 1
                    }
                    blocks.append(.table(MarkdownTable(
                        header: header,
                        alignments: normalize(alignments, count: header.count, filler: .leading),
                        rows: rows
                    )))
                    continue
                }
            }
            if let marker = listMarker(line), paragraph.isEmpty || !marker.content.isEmpty {
                flushParagraph()
                blocks.append(.list(parseList(lines, from: &index, first: marker, depth: depth)))
                continue
            }
            paragraph.append(line.trimmingCharacters(in: .whitespaces))
            index += 1
        }
        flushParagraph()
        return blocks
    }

    private static func parseList(_ lines: [String], from index: inout Int, first: ListMarker, depth: Int) -> MarkdownList {
        var list = MarkdownList(ordered: first.ordered, start: first.number, items: [])
        while index < lines.count, let marker = listMarker(lines[index]), marker.ordered == first.ordered {
            var itemLines = [marker.content]
            index += 1
            var previousBlank = false
            while index < lines.count {
                let line = lines[index]
                if isBlank(line) {
                    var next = index + 1
                    while next < lines.count, isBlank(lines[next]) { next += 1 }
                    guard next < lines.count, leadingSpaces(lines[next]) >= marker.contentIndent else { break }
                    // The whole blank run at once: the look-ahead already scanned it.
                    itemLines.append(contentsOf: repeatElement("", count: next - index))
                    previousBlank = true
                    index = next
                    continue
                }
                let indent = leadingSpaces(line)
                if indent >= marker.contentIndent {
                    itemLines.append(stripIndent(line, marker.contentIndent))
                } else if let nested = listMarker(line), nested.indent >= marker.indent + 2 {
                    itemLines.append(stripIndent(line, min(indent, marker.contentIndent)))
                } else if !previousBlank, listMarker(line) == nil, !startsBlock(line) {
                    itemLines.append(line.trimmingCharacters(in: .whitespaces))
                } else {
                    break
                }
                previousBlank = false
                index += 1
            }
            var task: Bool?
            if let first = itemLines.first, let (checked, rest) = taskPrefix(first) {
                task = checked
                itemLines[0] = rest
            }
            list.items.append(MarkdownList.Item(task: task, blocks: parseBlocks(itemLines, depth: depth + 1)))
            var next = index
            while next < lines.count, isBlank(lines[next]) { next += 1 }
            guard next < lines.count, let sibling = listMarker(lines[next]), sibling.ordered == first.ordered,
                  sibling.indent < first.contentIndent
            else { break }
            index = next
        }
        return list
    }

    // MARK: - Line recognizers

    private struct Fence {
        let marker: Character
        let count: Int
        let indent: Int
        let language: String?
    }

    struct ListMarker: Equatable {
        let indent: Int
        let ordered: Bool
        let number: Int
        let contentIndent: Int
        let content: String
    }

    private static func isBlank(_ line: String) -> Bool {
        line.allSatisfy { $0 == " " || $0 == "\t" }
    }

    private static func leadingSpaces(_ line: String) -> Int {
        line.prefix { $0 == " " }.count
    }

    private static func stripIndent(_ line: String, _ count: Int) -> String {
        String(line.dropFirst(min(count, leadingSpaces(line))))
    }

    private static func expandLeadingTabs(_ line: String) -> String {
        guard line.first == "\t" || (line.first == " " && line.contains("\t")) else { return line }
        var width = 0
        var rest = Substring(line)
        while let first = rest.first, first == " " || first == "\t" {
            width = first == "\t" ? (width / 4 + 1) * 4 : width + 1
            rest = rest.dropFirst()
        }
        return String(repeating: " ", count: width) + rest
    }

    /// A line that starts a non-paragraph block, so it cannot be a lazy continuation.
    private static func startsBlock(_ line: String) -> Bool {
        fenceOpen(line) != nil || heading(line) != nil || isRule(line) || quoteContent(line) != nil
            || listMarker(line).map { !$0.content.isEmpty } == true
    }

    private static func fenceOpen(_ line: String) -> Fence? {
        let indent = leadingSpaces(line)
        guard indent <= 3 else { return nil }
        let rest = line.dropFirst(indent)
        guard let marker = rest.first, marker == "`" || marker == "~" else { return nil }
        let count = rest.prefix { $0 == marker }.count
        guard count >= 3 else { return nil }
        let info = rest.dropFirst(count).trimmingCharacters(in: .whitespaces)
        if marker == "`", info.contains("`") { return nil }
        let language = info.split(separator: " ").first.map(String.init)
        return Fence(marker: marker, count: count, indent: indent, language: language)
    }

    private static func isFenceClose(_ line: String, fence: Fence) -> Bool {
        guard leadingSpaces(line) <= 3 else { return false }
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        return trimmed.count >= fence.count && trimmed.allSatisfy { $0 == fence.marker }
    }

    private static func heading(_ line: String) -> (level: Int, text: String)? {
        let indent = leadingSpaces(line)
        guard indent <= 3 else { return nil }
        let rest = line.dropFirst(indent)
        let level = rest.prefix { $0 == "#" }.count
        guard (1...6).contains(level) else { return nil }
        let after = rest.dropFirst(level)
        guard after.isEmpty || after.first == " " || after.first == "\t" else { return nil }
        var text = after.trimmingCharacters(in: .whitespaces)
        // A scan, not a regex: the regex backtracked quadratically on a long
        // run of spaces before the closing `#` run.
        let open = text.reversed().drop { $0 == "#" }.count
        if open < text.count {
            let kept = text.prefix(open)
            if kept.isEmpty || kept.last == " " || kept.last == "\t" {
                text = kept.trimmingCharacters(in: .whitespaces)
            }
        }
        return (level, text)
    }

    private static func isRule(_ line: String) -> Bool {
        guard leadingSpaces(line) <= 3 else { return false }
        let marks = line.filter { $0 != " " && $0 != "\t" }
        guard marks.count >= 3, let first = marks.first, "-*_".contains(first) else { return false }
        return marks.allSatisfy { $0 == first }
    }

    private static func quoteContent(_ line: String) -> String? {
        let indent = leadingSpaces(line)
        guard indent <= 3 else { return nil }
        let rest = line.dropFirst(indent)
        guard rest.first == ">" else { return nil }
        let content = rest.dropFirst()
        return String(content.first == " " ? content.dropFirst() : content)
    }

    static func listMarker(_ line: String) -> ListMarker? {
        let indent = leadingSpaces(line)
        let rest = line.dropFirst(indent)
        guard let first = rest.first else { return nil }
        var markerWidth: Int
        var ordered = false
        var number = 1
        if first == "-" || first == "*" || first == "+" {
            markerWidth = 1
        } else if first.isASCII, first.isNumber {
            let digits = rest.prefix { $0.isASCII && $0.isNumber }
            guard digits.count <= 9 else { return nil }
            let delimiter = rest.dropFirst(digits.count).first
            guard delimiter == "." || delimiter == ")" else { return nil }
            markerWidth = digits.count + 1
            ordered = true
            number = Int(digits) ?? 1
        } else {
            return nil
        }
        let afterMarker = rest.dropFirst(markerWidth)
        if afterMarker.isEmpty {
            return ListMarker(indent: indent, ordered: ordered, number: number, contentIndent: indent + markerWidth + 1, content: "")
        }
        guard afterMarker.first == " " else { return nil }
        var spaces = afterMarker.prefix { $0 == " " }.count
        let content = afterMarker.dropFirst(spaces)
        if spaces > 4 || content.isEmpty { spaces = 1 }
        markerWidth += spaces
        return ListMarker(
            indent: indent,
            ordered: ordered,
            number: number,
            contentIndent: indent + markerWidth,
            content: String(content)
        )
    }

    private static func taskPrefix(_ content: String) -> (Bool, String)? {
        for (prefix, checked) in [("[ ] ", false), ("[x] ", true), ("[X] ", true)] where content.hasPrefix(prefix) {
            return (checked, String(content.dropFirst(prefix.count)))
        }
        if content == "[ ]" { return (false, "") }
        if content == "[x]" || content == "[X]" { return (true, "") }
        return nil
    }

    // MARK: - Tables

    private static func tableSeparator(_ line: String) -> [MarkdownTable.Alignment]? {
        guard line.contains("|"), line.contains("-") else { return nil }
        let cells = splitRow(line)
        guard !cells.isEmpty else { return nil }
        var alignments: [MarkdownTable.Alignment] = []
        for cell in cells {
            let leading = cell.hasPrefix(":")
            let trailing = cell.hasSuffix(":")
            let dashes = cell.dropFirst(leading ? 1 : 0).dropLast(trailing && cell.count > 1 ? 1 : 0)
            guard !dashes.isEmpty, dashes.allSatisfy({ $0 == "-" }) else { return nil }
            alignments.append(leading && trailing ? .center : trailing ? .trailing : .leading)
        }
        return alignments
    }

    /// Splits a pipe row into trimmed cells; `\|` stays a literal pipe.
    private static func splitRow(_ line: String) -> [String] {
        var trimmed = Substring(line.trimmingCharacters(in: .whitespaces))
        if trimmed.hasPrefix("|") { trimmed = trimmed.dropFirst() }
        if trimmed.hasSuffix("|"), !trimmed.hasSuffix("\\|") { trimmed = trimmed.dropLast() }
        var cells: [String] = []
        var current = ""
        var escaped = false
        for character in trimmed {
            if escaped {
                current.append(character == "|" ? "|" : "\\\(character)")
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else if character == "|" {
                cells.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
            } else {
                current.append(character)
            }
        }
        if escaped { current.append("\\") }
        cells.append(current.trimmingCharacters(in: .whitespaces))
        return cells
    }

    private static func normalize<T>(_ values: [T], count: Int, filler: T) -> [T] {
        values.count >= count ? Array(values.prefix(count)) : values + Array(repeating: filler, count: count - values.count)
    }

    private static func normalize(_ values: [String], count: Int) -> [String] {
        normalize(values, count: count, filler: "")
    }
}
