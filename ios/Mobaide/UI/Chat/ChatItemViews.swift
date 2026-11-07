import SwiftUI
import UIKit

/// One timeline row. Equatable on its inputs so a streaming batch re-renders
/// only the rows whose item changed.
struct ChatItemRow: View, Equatable {
    let item: ChatItem
    /// The newest item while a turn runs: it gets the streaming affordances.
    let streaming: Bool
    let expanded: Bool
    let waitingForApproval: Bool
    let canRestart: Bool
    let onToggle: () -> Void
    let onRestart: () -> Void

    nonisolated static func == (lhs: ChatItemRow, rhs: ChatItemRow) -> Bool {
        lhs.item == rhs.item && lhs.streaming == rhs.streaming && lhs.expanded == rhs.expanded
            && lhs.waitingForApproval == rhs.waitingForApproval && lhs.canRestart == rhs.canRestart
    }

    var body: some View {
        switch item.body {
        case .user(let text, let attachments):
            ChatUserBubble(text: text, attachments: attachments)
        case .agent(let text):
            ChatAgentText(id: item.id, text: text, streaming: streaming)
        case .thought(let text):
            ChatThoughtRow(id: item.id, text: text, streaming: streaming, expanded: expanded, onToggle: onToggle)
        case .tool(let tool):
            ChatToolRow(
                id: item.id,
                tool: tool,
                expanded: expanded,
                waitingForApproval: waitingForApproval,
                onToggle: onToggle
            )
        case .notice(let severity, let title, let text):
            ChatNoticeRow(severity: severity, title: title, text: text)
        case .compaction(let status, let text):
            ChatCompactionRow(id: item.id, status: status, text: text, expanded: expanded, onToggle: onToggle)
        case .turnEnd(_, let text):
            ChatTurnEndRow(text: text)
        case .error(let text):
            ChatErrorRow(text: text, canRestart: canRestart, onRestart: onRestart)
        }
    }
}

private struct ChatTurnEndRow: View {
    let text: String

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(colors.textMuted)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 2)
    }
}

struct ChatUserBubble: View {
    let text: String
    let attachments: [ChatAttachment]

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack {
            Spacer(minLength: 48)
            VStack(alignment: .trailing, spacing: 6) {
                if !text.isEmpty {
                    Text(text)
                        .foregroundStyle(colors.text)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !attachments.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(Array(attachments.enumerated()), id: \.offset) { _, attachment in
                            Label(
                                attachment.kind == "image" ? "Image" : (attachment.name ?? "File"),
                                systemImage: attachment.kind == "image" ? "photo" : "doc"
                            )
                            .font(.caption)
                            .foregroundStyle(colors.textMuted)
                            .padding(EdgeInsets(top: 3, leading: 8, bottom: 3, trailing: 8))
                            .background(colors.bg2)
                            .pixelRing(colors.rule)
                        }
                    }
                }
            }
            .padding(EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12))
            .background(colors.surface2)
            .pixelRing(MobaideColorScheme.brand2)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("You: \(text)")
    }
}

struct ChatAgentText: View {
    let id: String
    let text: String
    let streaming: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !text.isEmpty {
                ChatMarkdownView(text: text, cacheKey: id)
                    .textSelection(.enabled)
            }
            if streaming {
                PixelLoadingBlock(width: 8, height: 14)
                    .accessibilityLabel("Writing")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct ChatThoughtRow: View {
    let id: String
    let text: String
    let streaming: Bool
    let expanded: Bool
    let onToggle: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: onToggle) {
                HStack(spacing: 8) {
                    Image(systemName: "brain")
                        .foregroundStyle(streaming ? colors.accent : colors.textMuted)
                        .symbolEffect(.pulse, isActive: streaming)
                    Text(streaming ? "Thinking…" : "Thinking")
                        .font(.subheadline)
                        .foregroundStyle(colors.textMuted)
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(colors.gray)
                        .rotationEffect(.degrees(expanded ? 90 : 0))
                    Spacer(minLength: 0)
                }
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(streaming ? "Thinking" : "Thought")
            .accessibilityHint(expanded ? "Collapse" : "Expand")
            if expanded, !text.isEmpty {
                ChatMarkdownView(text: text, cacheKey: id, muted: true)
                    .font(.callout)
                    .textSelection(.enabled)
                    .padding(.leading, 12)
                    .overlay(alignment: .leading) {
                        Rectangle().fill(colors.rule).frame(width: 2)
                    }
            }
        }
    }
}

enum ChatToolStyle {
    static func icon(_ kind: String) -> String {
        switch kind {
        case "read": return "eye"
        case "edit": return "pencil"
        case "delete": return "trash"
        case "move": return "arrow.right"
        case "search": return "magnifyingglass"
        case "execute": return "terminal"
        case "think": return "brain"
        case "fetch": return "globe"
        case "switch_mode": return "flag"
        default: return "wrench"
        }
    }
}

/// The tool call's status mark: hollow, working, done, failed, cancelled.
struct ChatToolStatusIcon: View {
    let status: String

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Group {
            switch status {
            case "in_progress":
                PixelLoadingBlock(width: 7, height: 12)
            case "completed":
                Image(systemName: "checkmark").foregroundStyle(colors.ok)
            case "failed":
                Image(systemName: "xmark").foregroundStyle(colors.danger)
            case "cancelled":
                Image(systemName: "circle.slash").foregroundStyle(colors.gray)
            default:
                Image(systemName: "circle").foregroundStyle(colors.gray)
            }
        }
        .font(.footnote.weight(.semibold))
        .frame(width: 18)
        .accessibilityLabel(Self.label(status))
    }

    static func label(_ status: String) -> String {
        switch status {
        case "in_progress": return "Running"
        case "completed": return "Done"
        case "failed": return "Failed"
        case "cancelled": return "Cancelled"
        default: return "Pending"
        }
    }
}

struct ChatToolRow: View {
    let id: String
    let tool: ChatToolCall
    let expanded: Bool
    let waitingForApproval: Bool
    let onToggle: () -> Void

    @Environment(\.mobaideColors) private var colors
    @State private var inputShown = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onToggle) {
                header
            }
            .buttonStyle(.plain)
            .accessibilityHint(expanded ? "Collapse" : "Expand")
            if expanded {
                PixelDivider()
                details
                    .padding(EdgeInsets(top: 10, leading: 12, bottom: 12, trailing: 12))
            }
        }
        .background(colors.bg2)
        .pixelRing(waitingForApproval ? MobaideColorScheme.brand2 : colors.surface2)
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: ChatToolStyle.icon(tool.kind))
                .font(.subheadline)
                .foregroundStyle(colors.textMuted)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(tool.title.isEmpty ? "Tool call" : tool.title)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(colors.text)
                    .lineLimit(1)
                if let subtitle = tool.subtitle, !subtitle.isEmpty, subtitle != tool.title {
                    Text(subtitle)
                        .font(.caption.monospaced())
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                if tool.status == "failed", let line = tool.firstTextLine, !line.isEmpty {
                    Text(line)
                        .font(.caption)
                        .foregroundStyle(colors.danger)
                        .lineLimit(2)
                }
                if waitingForApproval {
                    Text("Waiting for approval")
                        .chromeText()
                        .foregroundStyle(colors.accent)
                }
            }
            Spacer(minLength: 4)
            if let stats = tool.diffStats {
                ChatDiffStat(added: stats.added, removed: stats.removed)
            }
            ChatToolStatusIcon(status: tool.status)
        }
        .padding(EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 12))
        .frame(minHeight: 46)
        .contentShape(Rectangle())
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(tool.content.enumerated()), id: \.offset) { index, part in
                ChatContentPartView(part: part, cacheKey: "\(id)#\(index)")
            }
            if !tool.locations.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(tool.locations.enumerated()), id: \.offset) { _, location in
                        ChatCopyableLine(text: location.label)
                    }
                }
            }
            if let input = tool.input, !input.isEmpty {
                Button {
                    inputShown.toggle()
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.semibold))
                            .rotationEffect(.degrees(inputShown ? 90 : 0))
                        Text("Input")
                            .chromeText()
                    }
                    .foregroundStyle(colors.textMuted)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if inputShown {
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(input)
                            .font(.caption.monospaced())
                            .foregroundStyle(colors.text)
                            .fixedSize(horizontal: true, vertical: false)
                            .textSelection(.enabled)
                            .padding(8)
                    }
                    .background(colors.surface)
                    .pixelRing(colors.surface2)
                }
            }
            if tool.content.isEmpty, tool.locations.isEmpty, tool.input?.isEmpty != false {
                Text("No details")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
        }
    }
}

/// A mono, muted line with a copy button (paths, ids).
struct ChatCopyableLine: View {
    let text: String

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 4) {
            Text(text)
                .font(.caption.monospaced())
                .foregroundStyle(colors.textMuted)
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
            Spacer(minLength: 4)
            Button {
                Clipboard.copy(text)
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            } label: {
                Image(systemName: "doc.on.doc")
                    .font(.caption)
                    .foregroundStyle(colors.accent)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Copy \(text)")
        }
    }
}

struct ChatContentPartView: View {
    let part: ChatContentPart
    let cacheKey: String

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        switch part {
        case .text(let text, let truncated):
            VStack(alignment: .leading, spacing: 4) {
                ChatMarkdownView(text: text, cacheKey: cacheKey)
                    .font(.callout)
                    .textSelection(.enabled)
                if truncated {
                    Text("Output truncated")
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                }
            }
        case .diff(let diff):
            ChatDiffView(diff: diff)
        case .image(_, let data):
            if let data {
                ChatInlineImage(base64: data, cacheKey: cacheKey)
            } else {
                ChatResourceRow(icon: "photo", title: "Image", detail: nil, url: nil)
            }
        case .link(let uri, let name):
            ChatResourceRow(icon: "link", title: name ?? uri, detail: name == nil ? nil : uri, url: Self.safeURL(uri))
        case .resource(let uri, let name, let text):
            VStack(alignment: .leading, spacing: 4) {
                ChatResourceRow(icon: "doc", title: name ?? uri, detail: name == nil ? nil : uri, url: Self.safeURL(uri))
                if let text, !text.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(text.split(separator: "\n", omittingEmptySubsequences: false).prefix(20).joined(separator: "\n"))
                            .font(.caption.monospaced())
                            .foregroundStyle(colors.text)
                            .fixedSize(horizontal: true, vertical: false)
                            .padding(8)
                    }
                    .background(colors.surface)
                    .pixelRing(colors.surface2)
                }
            }
        case .terminal:
            ChatResourceRow(icon: "terminal", title: "Terminal output", detail: nil, url: nil)
        }
    }

    static func safeURL(_ uri: String) -> URL? {
        guard let url = URL(string: uri), let scheme = url.scheme?.lowercased(),
              ["http", "https", "mailto"].contains(scheme)
        else { return nil }
        return url
    }
}

/// An agent image part, decoded off the main actor; an oversized one shows a
/// row instead of being decoded.
private struct ChatInlineImage: View {
    let base64: String
    let cacheKey: String

    private static let maxSide: CGFloat = 280

    @Environment(\.mobaideColors) private var colors
    @Environment(\.displayScale) private var displayScale
    @State private var decoded: (key: String, outcome: ChatImageDecoder.Outcome)?

    /// Decoded at the drawn size, not the source size, so the cache holds many.
    private var maxPixelSize: Int { Int((Self.maxSide * displayScale).rounded(.up)) }

    private var key: String { "\(cacheKey)|\(base64.utf8.count)|\(maxPixelSize)" }

    private var outcome: ChatImageDecoder.Outcome? {
        if let decoded, decoded.key == key { return decoded.outcome }
        return ChatImageCache.outcome(for: key)
    }

    var body: some View {
        Group {
            switch outcome {
            case .image(let image)?:
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: Self.maxSide, maxHeight: Self.maxSide, alignment: .leading)
                    .pixelRing(colors.surface2)
                    .accessibilityLabel("Image")
            case .tooLarge?:
                ChatResourceRow(icon: "photo", title: "Image too large", detail: nil, url: nil)
            case .invalid?:
                ChatResourceRow(icon: "photo", title: "Image", detail: nil, url: nil)
            case nil:
                PixelLoadingBlock(width: 8, height: 14)
                    .frame(width: 120, height: 80)
                    .background(colors.surface)
                    .pixelRing(colors.surface2)
                    .accessibilityLabel("Loading image")
            }
        }
        .task(id: key) {
            let key = self.key
            if let cached = ChatImageCache.outcome(for: key) {
                decoded = (key, cached)
                return
            }
            let outcome = await ChatImageDecoder.decode(base64: base64, maxPixelSize: maxPixelSize)
            guard !Task.isCancelled else { return }
            ChatImageCache.store(outcome, for: key)
            decoded = (key, outcome)
        }
    }
}

private struct ChatResourceRow: View {
    let icon: String
    let title: String
    let detail: String?
    let url: URL?

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        let row = HStack(spacing: 8) {
            Image(systemName: icon)
                .foregroundStyle(colors.textMuted)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(.footnote)
                    .foregroundStyle(url == nil ? colors.text : colors.accent)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let detail {
                    Text(detail)
                        .font(.caption2.monospaced())
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(colors.surface)
        .pixelRing(colors.surface2)
        if let url {
            Link(destination: url) { row }
        } else {
            row
        }
    }
}

struct ChatNoticeRow: View {
    let severity: String
    let title: String
    let text: String

    @Environment(\.mobaideColors) private var colors

    private var tone: PixelTone {
        switch severity {
        case "warning": return .warning
        case "error": return .danger
        case "info": return .accent
        default: return .muted
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: severity == "error" ? "exclamationmark.octagon" : severity == "warning" ? "exclamationmark.triangle" : "info.circle")
                .foregroundStyle(colors.color(tone))
            VStack(alignment: .leading, spacing: 3) {
                if !title.isEmpty {
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(tone == .muted ? colors.text : colors.color(tone))
                }
                if !text.isEmpty {
                    Text(text)
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                        .textSelection(.enabled)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12))
        .background(colors.surface)
        .pixelRing(colors.ring(tone))
        .accessibilityElement(children: .combine)
    }
}

struct ChatCompactionRow: View {
    let id: String
    let status: String
    let text: String
    let expanded: Bool
    let onToggle: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(action: onToggle) {
                HStack(spacing: 8) {
                    Rectangle().fill(colors.surface2).frame(height: PixelMetrics.ring)
                    Text(status == "failed" ? "Compaction failed" : "Context compacted")
                        .chromeText()
                        .foregroundStyle(status == "failed" ? colors.danger : colors.textMuted)
                        .fixedSize()
                    if status == "in_progress" {
                        PixelLoadingBlock(width: 6, height: 10)
                    }
                    Rectangle().fill(colors.surface2).frame(height: PixelMetrics.ring)
                }
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(text.isEmpty)
            .accessibilityHint(text.isEmpty ? "" : (expanded ? "Hide the summary" : "Show the summary"))
            if expanded, !text.isEmpty {
                ChatMarkdownView(text: text, cacheKey: id, muted: true)
                    .font(.callout)
                    .textSelection(.enabled)
            }
        }
    }
}

struct ChatErrorRow: View {
    let text: String
    let canRestart: Bool
    let onRestart: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(colors.danger)
                Text(text)
                    .font(.footnote)
                    .foregroundStyle(colors.danger)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            if canRestart {
                Button("Restart", action: onRestart)
                    .buttonStyle(.pixelSmall)
            }
        }
        .padding(EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12))
        .background(colors.danger.opacity(0.08))
        .pixelRing(colors.danger)
    }
}
