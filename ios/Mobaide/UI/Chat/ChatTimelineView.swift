import SwiftUI

private let chatBottomId = "chat-bottom"

/// The scrolling timeline, following the bottom until the user scrolls away.
/// A `List`, not a `LazyVStack`, which re-places forever when rows of very
/// different heights cross the visible edge while the last row grows. The 1pt
/// sentinel row only updates plain bookkeeping: a state write at the edge
/// would flip it back.
struct ChatTimelineView: View {
    let model: ChatSessionModel
    let agentLabel: String
    var focusView = false

    @Environment(\.mobaideColors) private var colors
    @State private var following = true
    @State private var unseen = false
    @State private var tracker = ScrollTracker()
    @State private var press = PressTracker()

    var body: some View {
        ScrollViewReader { proxy in
            List {
                if model.truncated {
                    Text("Earlier messages are not shown.")
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                        .frame(maxWidth: .infinity)
                        .chatTimelineRow()
                }
                if model.items.isEmpty {
                    placeholder
                        .chatTimelineRow()
                }
                let lastId = model.items.last?.id
                let lastErrorId = model.items.last { if case .error = $0.body { return true } else { return false } }?.id
                let waiting = model.waitingToolIds
                let canRestart = model.phase == .error && model.isLive && !model.restarting
                let rows = ChatFocusView.rows(
                    model.items,
                    focus: focusView,
                    running: model.isRunning,
                    waitingToolIds: waiting,
                    open: model.expandedItems
                )
                ForEach(rows) { row in
                    switch row {
                    case .steps(let id, let count, let live, let current, let open):
                        ChatStepsRow(count: count, live: live, current: current, open: open) {
                            model.toggleExpanded(id)
                        }
                        .chatTimelineRow()
                    case .item(let item, let nested):
                        ChatItemRow(
                            item: item,
                            streaming: item.id == lastId && model.isRunning,
                            expanded: model.expandedItems.contains(item.id),
                            waitingForApproval: waiting.contains(item.id),
                            canRestart: canRestart && item.id == lastErrorId,
                            onToggle: { model.toggleExpanded(item.id) },
                            onRestart: { model.restart() }
                        )
                        .equatable()
                        .chatNestedStep(nested, rule: colors.surface2)
                        .chatTimelineRow()
                    }
                }
                Color.clear
                    .frame(height: 1)
                    .id(chatBottomId)
                    .onAppear { sentinelVisible(true) }
                    .onDisappear { sentinelVisible(false) }
                    .chatTimelineRow(insets: EdgeInsets())
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .environment(\.defaultMinListRowHeight, 1)
            .scrollDismissesKeyboard(.interactively)
            // A tap hides the keyboard, as outside the field in other chat
            // apps; a long press is a text selection and keeps it. Both are
            // simultaneous, so row buttons and text selection keep their taps.
            .simultaneousGesture(TapGesture().onEnded {
                if press.heldLong {
                    press.heldLong = false
                } else {
                    ChatKeyboard.hide()
                }
            })
            .simultaneousGesture(
                LongPressGesture(minimumDuration: 0.5)
                    .onChanged { _ in press.heldLong = false }
                    .onEnded { _ in press.heldLong = true }
            )
            .modifier(UserScrollPhase(
                onStart: { tracker.setDriving(true) },
                onEnd: { flung in userScrollEnded(flung: flung) }
            ))
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { _ in
                if following, !tracker.userDriven { scrollToBottom(proxy) }
            }
            .onChange(of: model.itemsVersion) { _, _ in
                if following {
                    if !tracker.userDriven { scrollToBottom(proxy) }
                } else if !unseen {
                    unseen = true
                }
            }
            .onChange(of: model.followRequests) { _, _ in landAtBottom(proxy) }
            .onChange(of: model.hasSnapshot) { _, loaded in
                if loaded { landAtBottom(proxy) }
            }
            .onAppear { landAtBottom(proxy) }
            .overlay(alignment: .bottomTrailing) {
                if !following {
                    jumpButton(proxy)
                }
            }
        }
    }

    private func sentinelVisible(_ visible: Bool) {
        tracker.atBottom = visible
    }

    /// The user let go: follow again only if they left the view at the
    /// bottom. A fling (iOS 17, no scroll phases) keeps moving after the
    /// finger lifts, so its verdict waits until it has settled.
    private func userScrollEnded(flung: Bool) {
        tracker.setDriving(false)
        guard flung else {
            settleFollowing()
            return
        }
        tracker.coast(for: 0.8)
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(850))
            settleFollowing()
        }
    }

    private func settleFollowing() {
        guard !tracker.userDriven else { return }
        let atBottom = tracker.atBottom
        if following != atBottom {
            following = atBottom
        }
        if atBottom, unseen {
            unseen = false
        }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        var transaction = Transaction()
        transaction.disablesAnimations = true
        withTransaction(transaction) {
            proxy.scrollTo(chatBottomId, anchor: .bottom)
        }
    }

    /// Lazy rows are estimated until laid out, so the first jump can land
    /// short; a second pass after layout settles it.
    private func landAtBottom(_ proxy: ScrollViewProxy) {
        if !following { following = true }
        if unseen { unseen = false }
        scrollToBottom(proxy)
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(150))
            if following { scrollToBottom(proxy) }
        }
    }

    private func jumpButton(_ proxy: ScrollViewProxy) -> some View {
        Button {
            landAtBottom(proxy)
        } label: {
            Image(systemName: "arrow.down")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(colors.text)
                .frame(width: 44, height: 44)
                .background(colors.bg2)
                .pixelRing(unseen ? MobaideColorScheme.brand2 : colors.surface2)
                .pixelShadow(MobaideColorScheme.btnShadow)
                .overlay(alignment: .topTrailing) {
                    if unseen {
                        Rectangle()
                            .fill(colors.accent)
                            .frame(width: 10, height: 10)
                            .offset(x: 3, y: -3)
                    }
                }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(unseen ? "Jump to latest, new messages" : "Jump to latest")
        .padding(16)
    }

    @ViewBuilder
    private var placeholder: some View {
        if model.isLoading {
            VStack(spacing: 14) {
                PixelLoadingBlock(width: 10, height: 18)
                Text(model.phase == .loading ? "Opening the session…" : "Starting \(agentLabel)…")
                    .font(.subheadline)
                    .foregroundStyle(colors.textMuted)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 80)
        } else if model.phase == .error {
            ChatErrorRow(
                text: model.state.error ?? "The agent stopped.",
                canRestart: model.isLive && !model.restarting,
                onRestart: { model.restart() }
            )
            .padding(.top, 40)
        } else if model.phase == .ready || model.phase == .running {
            VStack(spacing: 10) {
                Text(model.state.agent?.displayName ?? agentLabel)
                    .pageTitle()
                    .multilineTextAlignment(.center)
                if let cwd = model.state.cwd {
                    Text(cwd)
                        .font(.caption.monospaced())
                        .foregroundStyle(colors.textMuted)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Text(model.state.commands.isEmpty
                    ? "Send a message to start."
                    : "Send a message to start. Type / for commands.")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
            .padding(PixelMetrics.emptyStateInsets)
            .background(colors.bg2)
            .pixelRing(colors.surface2)
            .padding(.top, 40)
        }
    }
}

/// Plain bookkeeping read only from callbacks, so updating it never
/// invalidates the view.
private extension View {
    /// A step under its open steps row: indented behind a rule.
    @ViewBuilder
    func chatNestedStep(_ nested: Bool, rule: Color) -> some View {
        if nested {
            padding(.leading, 14)
                .overlay(alignment: .leading) {
                    Rectangle().fill(rule).frame(width: PixelMetrics.ring)
                }
        } else {
            self
        }
    }

    /// A timeline row inside the `List`: no separator, no cell background.
    func chatTimelineRow(insets: EdgeInsets = EdgeInsets(top: 6, leading: 16, bottom: 6, trailing: 16)) -> some View {
        listRowInsets(insets)
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)
    }
}

private final class ScrollTracker {
    var atBottom = true
    private var driving = false
    /// iOS 17 has no scroll phases: a drag's fling keeps going after the
    /// finger lifts, so it still counts as the user's for a moment.
    private var drivenUntil: Date?

    var userDriven: Bool {
        driving || (drivenUntil.map { Date() < $0 } ?? false)
    }

    func setDriving(_ value: Bool) {
        driving = value
    }

    func coast(for seconds: TimeInterval) {
        drivenUntil = Date().addingTimeInterval(seconds)
    }
}

/// Whether the current touch was held past the long-press time. Reset at
/// touch-down and by the tap it holds back, so it cannot swallow a later tap.
private final class PressTracker {
    var heldLong = false
}

/// Reports a user scroll: its start, and its end once any fling has stopped
/// (iOS 18 scroll phases). Programmatic scrolls are not reported. iOS 17 has
/// no phases: a drag stands in, and its end is flagged as a possible fling.
private struct UserScrollPhase: ViewModifier {
    let onStart: () -> Void
    let onEnd: (_ flung: Bool) -> Void

    func body(content: Content) -> some View {
        if #available(iOS 18.0, *) {
            content.onScrollPhaseChange { old, new in
                let wasUser = Self.isUser(old)
                let isUser = Self.isUser(new)
                if isUser, !wasUser {
                    onStart()
                } else if wasUser, !isUser {
                    onEnd(false)
                }
            }
        } else {
            content.simultaneousGesture(
                DragGesture(minimumDistance: 6)
                    .onChanged { _ in onStart() }
                    .onEnded { _ in onEnd(true) }
            )
        }
    }

    @available(iOS 18.0, *)
    private static func isUser(_ phase: ScrollPhase) -> Bool {
        switch phase {
        case .tracking, .interacting, .decelerating: return true
        default: return false
        }
    }
}
