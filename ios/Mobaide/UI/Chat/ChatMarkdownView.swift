import SwiftUI
import UIKit

/// Inline spans for one block: Foundation's inline-only Markdown, then the
/// safety pass. Raw HTML stays literal text; only `http`, `https` and `mailto`
/// links stay tappable; an image becomes a link labelled with its alt text and
/// is never loaded.
@MainActor
enum MarkdownInline {
    private static var cache: [String: AttributedString] = [:]
    private static var cacheBytes = 0
    private static let cacheLimit = 4000
    private static let cacheByteLimit = 2_000_000
    /// A paragraph that is still streaming would store every prefix of itself.
    private static let maxCachedSourceBytes = 8192
    private static let allowedSchemes: Set<String> = ["http", "https", "mailto"]
    private static let codeBackground = Color(uiColor: MobaideColorScheme.dynamic(\.surface2))

    static func render(_ source: String) -> AttributedString {
        let bytes = source.utf8.count
        guard bytes <= maxCachedSourceBytes else { return build(source) }
        if let cached = cache[source] { return cached }
        let rendered = build(source)
        if cache.count >= cacheLimit || cacheBytes + bytes > cacheByteLimit {
            cache.removeAll(keepingCapacity: true)
            cacheBytes = 0
        }
        cache[source] = rendered
        cacheBytes += bytes
        return rendered
    }

    private static func build(_ source: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: false,
            interpretedSyntax: .inlineOnlyPreservingWhitespace,
            failurePolicy: .returnPartiallyParsedIfPossible
        )
        guard var text = try? AttributedString(markdown: source, options: options) else {
            return AttributedString(source)
        }
        for run in text.runs {
            let range = run.range
            if let image = run.imageURL {
                text[range].imageURL = nil
                text[range].link = isAllowed(image) ? image : nil
            } else if let link = run.link, !isAllowed(link) {
                text[range].link = nil
            }
            if run.inlinePresentationIntent?.contains(.code) == true {
                text[range].backgroundColor = codeBackground
            }
        }
        return text
    }

    private static func isAllowed(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else { return false }
        return allowedSchemes.contains(scheme)
    }
}

/// Parsed blocks per text, so a row re-renders without re-parsing. Keyed by the
/// owner (item id, content part) so a streaming item replaces its own entry.
@MainActor
enum MarkdownDocumentCache {
    private static var entries: [String: (source: String, blocks: [MarkdownBlock])] = [:]
    private static let limit = 600

    static func blocks(for key: String?, text: String) -> [MarkdownBlock] {
        guard let key else { return MarkdownParser.parse(text) }
        if let entry = entries[key], entry.source == text { return entry.blocks }
        let blocks = MarkdownParser.parse(text)
        if entries.count >= limit { entries.removeAll(keepingCapacity: true) }
        entries[key] = (text, blocks)
        return blocks
    }
}

/// Renders chat Markdown. `cacheKey` names the owner of the text for parse
/// memoization; `muted` draws the whole block in the muted colour (thoughts).
struct ChatMarkdownView: View {
    let text: String
    var cacheKey: String?
    var muted = false

    var body: some View {
        MarkdownBlocksView(blocks: MarkdownDocumentCache.blocks(for: cacheKey, text: text), muted: muted)
    }
}

private struct MarkdownBlocksView: View {
    let blocks: [MarkdownBlock]
    let muted: Bool

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
    }

    private var textColor: Color { muted ? colors.textMuted : colors.text }

    @ViewBuilder
    private func blockView(_ block: MarkdownBlock) -> some View {
        switch block {
        case .paragraph(let source):
            Text(MarkdownInline.render(source))
                .foregroundStyle(textColor)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .heading(let level, let source):
            Text(MarkdownInline.render(source))
                .font(Self.headingFont(level))
                .foregroundStyle(textColor)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, level <= 2 ? 4 : 2)
                .accessibilityAddTraits(.isHeader)
        case .code(let language, let code):
            MarkdownCodeBlock(language: language, code: code)
        case .list(let list):
            listView(list)
        case .quote(let inner):
            HStack(alignment: .top, spacing: 10) {
                Rectangle()
                    .fill(colors.rule)
                    .frame(width: 3)
                MarkdownBlocksView(blocks: inner, muted: true)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .rule:
            PixelDivider()
                .padding(.vertical, 4)
        case .table(let table):
            MarkdownTableView(table: table)
        }
    }

    private func listView(_ list: MarkdownList) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(list.items.enumerated()), id: \.offset) { offset, item in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    listMarker(list: list, offset: offset, item: item)
                    MarkdownBlocksView(blocks: item.blocks, muted: muted || item.task == true)
                        .strikethrough(item.task == true, color: colors.textMuted)
                }
            }
        }
    }

    @ViewBuilder
    private func listMarker(list: MarkdownList, offset: Int, item: MarkdownList.Item) -> some View {
        if let checked = item.task {
            Image(systemName: checked ? "checkmark.square.fill" : "square")
                .font(.footnote)
                .foregroundStyle(checked ? colors.accent : colors.gray)
                .accessibilityLabel(checked ? "Done" : "Not done")
        } else if list.ordered {
            Text("\(list.start + offset).")
                .monospacedDigit()
                .foregroundStyle(colors.textMuted)
        } else {
            Text("•")
                .foregroundStyle(colors.textMuted)
        }
    }

    private static func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .title3.weight(.bold)
        case 2: return .headline
        default: return .subheadline.weight(.semibold)
        }
    }
}

/// A fenced code block: language label and Copy over a sideways-scrolling body.
struct MarkdownCodeBlock: View {
    let language: String?
    let code: String

    @Environment(\.mobaideColors) private var colors
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text(language ?? "code")
                    .font(.caption.monospaced())
                    .foregroundStyle(colors.textMuted)
                Spacer(minLength: 8)
                Button {
                    Clipboard.copy(code)
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    copied = true
                } label: {
                    Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc")
                        .labelStyle(.titleAndIcon)
                        .font(.caption)
                        .foregroundStyle(colors.accent)
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Copy code")
            }
            .padding(.horizontal, 10)
            .background(colors.surface2)
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(MobaideTypography.code)
                    .foregroundStyle(colors.text)
                    .fixedSize(horizontal: true, vertical: false)
                    .padding(10)
            }
        }
        .background(colors.surface)
        .pixelRing(colors.surface2)
        .task(id: copied) {
            guard copied else { return }
            try? await Task.sleep(for: .seconds(1.5))
            copied = false
        }
    }
}

private struct MarkdownTableView: View {
    let table: MarkdownTable

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow {
                    ForEach(Array(table.header.enumerated()), id: \.offset) { column, cell in
                        cellView(cell, column: column, header: true)
                    }
                }
                ForEach(Array(table.rows.enumerated()), id: \.offset) { _, row in
                    GridRow {
                        ForEach(Array(row.enumerated()), id: \.offset) { column, cell in
                            cellView(cell, column: column, header: false)
                        }
                    }
                }
            }
            .pixelRing(colors.surface2)
        }
    }

    private func cellView(_ source: String, column: Int, header: Bool) -> some View {
        let alignment = column < table.alignments.count ? table.alignments[column] : .leading
        return Text(MarkdownInline.render(source))
            .font(header ? .footnote.weight(.semibold) : .footnote)
            .foregroundStyle(colors.text)
            .multilineTextAlignment(Self.textAlignment(alignment))
            .frame(maxWidth: 280, alignment: Self.frameAlignment(alignment))
            .fixedSize(horizontal: false, vertical: true)
            .padding(EdgeInsets(top: 6, leading: 10, bottom: 6, trailing: 10))
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: Self.frameAlignment(alignment))
            .background(header ? colors.surface : Color.clear)
            .overlay(Rectangle().stroke(colors.surface2, lineWidth: 1))
    }

    private static func textAlignment(_ alignment: MarkdownTable.Alignment) -> TextAlignment {
        switch alignment {
        case .leading: return .leading
        case .center: return .center
        case .trailing: return .trailing
        }
    }

    private static func frameAlignment(_ alignment: MarkdownTable.Alignment) -> Alignment {
        switch alignment {
        case .leading: return .leading
        case .center: return .center
        case .trailing: return .trailing
        }
    }
}

/// A unified diff body with a file header: no wrapping, one sideways scroll
/// for the whole block, tinted `+`/`-` rows, the first lines until expanded.
struct ChatDiffView: View {
    let diff: ChatDiff
    var collapsedLineCount = 30

    @Environment(\.mobaideColors) private var colors
    @State private var expanded = false
    @State private var viewportWidth: CGFloat = 0

    private static let maxLines = 2000
    private static let font = UIFont.monospacedSystemFont(ofSize: 12, weight: .regular)
    private static let charWidth = ("M" as NSString).size(withAttributes: [.font: font]).width

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if diff.diff.isEmpty {
                Text(diff.truncated ? "Too large to show a diff" : "No changes")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
                    .padding(10)
            } else {
                bodyLines
            }
        }
        .background(colors.surface)
        .pixelRing(colors.surface2)
    }

    private var fileName: String {
        (diff.path as NSString).lastPathComponent
    }

    private var directory: String {
        let parent = (diff.path as NSString).deletingLastPathComponent
        return parent.isEmpty ? "" : parent
    }

    private var header: some View {
        HStack(spacing: 6) {
            Text(fileName.isEmpty ? diff.path : fileName)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(colors.text)
                .lineLimit(1)
                .layoutPriority(1)
            if !directory.isEmpty {
                Text(directory)
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 4)
            if diff.newFile {
                Text("New file")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(colors.accent)
                    .padding(EdgeInsets(top: 2, leading: 6, bottom: 2, trailing: 6))
                    .background(colors.surface)
                    .pixelRing(colors.ring(.accent))
            }
            ChatDiffStat(added: diff.added, removed: diff.removed)
        }
        .padding(EdgeInsets(top: 8, leading: 10, bottom: 8, trailing: 10))
        .background(colors.surface2.opacity(0.5))
    }

    @ViewBuilder
    private var bodyLines: some View {
        let all = diff.diff.split(separator: "\n", omittingEmptySubsequences: false)
        let limit = expanded ? Self.maxLines : collapsedLineCount
        let shown = all.prefix(limit).map { GitDiffDisplay.renderableText(String($0)) }
        let columns = shown.map(\.count).max() ?? 0
        let width = max(CGFloat(columns) * Self.charWidth + 20, viewportWidth)
        ScrollView(.horizontal, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(shown.enumerated()), id: \.offset) { _, line in
                    Text(line.isEmpty ? " " : line)
                        .font(Font(Self.font))
                        .foregroundStyle(lineColor(line))
                        .lineLimit(1)
                        .fixedSize()
                        .padding(.horizontal, 10)
                        .padding(.vertical, 1)
                        .frame(width: width, alignment: .leading)
                        .background(lineBackground(line))
                }
            }
            .padding(.vertical, 4)
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { viewportWidth = $0 }
        .textSelection(.enabled)
        if all.count > collapsedLineCount, !expanded {
            Button {
                expanded = true
            } label: {
                Text("Show all \(min(all.count, Self.maxLines)) lines")
                    .font(.caption)
                    .foregroundStyle(colors.accent)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        if diff.truncated || (expanded && all.count > Self.maxLines) {
            Text("Diff truncated")
                .font(.caption)
                .foregroundStyle(colors.textMuted)
                .padding(EdgeInsets(top: 4, leading: 10, bottom: 8, trailing: 10))
        }
    }

    private func lineColor(_ line: String) -> Color {
        line.hasPrefix("@@") ? colors.textMuted : colors.text
    }

    private func lineBackground(_ line: String) -> Color {
        if line.hasPrefix("@@") { return colors.surface2.opacity(0.6) }
        if line.hasPrefix("+") { return colors.ok.opacity(0.16) }
        if line.hasPrefix("-") { return colors.danger.opacity(0.16) }
        return .clear
    }
}

/// `+N −M` in the ok and danger colours.
struct ChatDiffStat: View {
    let added: Int
    let removed: Int

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 4) {
            Text("+\(added)").foregroundStyle(colors.ok)
            Text("−\(removed)").foregroundStyle(colors.danger)
        }
        .font(.caption.monospacedDigit().weight(.semibold))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(added) added, \(removed) removed")
    }
}
