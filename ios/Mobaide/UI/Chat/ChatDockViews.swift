import SwiftUI

/// Content at its own height up to `maxHeight`, scrolling past it. Never
/// taller than its content, so it does not take a parent's spare space. The
/// content's height depends only on the width, so measuring it cannot feed back.
struct ChatCappedScroll<Content: View>: View {
    let maxHeight: CGFloat
    /// When set, content cut off by the cap fades into this colour, so the
    /// user can see that more is below.
    var fadeColor: Color? = nil
    /// When set, the area shrinks below `maxHeight` (down to nothing) if the
    /// parent offers less height, so that its siblings keep their full size.
    var yields: Bool = false
    @ViewBuilder let content: () -> Content

    @State private var contentHeight: CGFloat = 0
    @State private var shownHeight: CGFloat = 0

    var body: some View {
        let height = min(contentHeight, maxHeight)
        // A yielding area is judged by its laid-out height, once measured.
        let overflows = yields
            ? shownHeight > 0 && contentHeight > shownHeight + 0.5
            : contentHeight > maxHeight + 0.5
        ScrollView {
            content()
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { contentHeight = $0 }
        }
        .scrollBounceBehavior(.basedOnSize)
        .scrollIndicatorsFlash(trigger: overflows)
        .frame(minHeight: yields ? 0 : height, idealHeight: height, maxHeight: height)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { shownHeight = $0 }
        .overlay(alignment: .bottom) {
            if overflows, let fadeColor {
                LinearGradient(colors: [fadeColor.opacity(0), fadeColor], startPoint: .top, endPoint: .bottom)
                    .frame(height: yields ? min(16, shownHeight) : 16)
                    .allowsHitTesting(false)
            }
        }
    }
}

/// `Plan · done/total` with the running step; expands into the whole list.
struct ChatPlanBar: View {
    let plan: [ChatPlanEntry]

    @Environment(\.mobaideColors) private var colors
    @State private var expanded = false

    private var done: Int { plan.filter { $0.status == "completed" }.count }
    private var current: ChatPlanEntry? {
        plan.first { $0.status == "in_progress" } ?? plan.first { $0.status == "pending" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                expanded.toggle()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "list.bullet")
                        .foregroundStyle(colors.accent)
                    Text("Plan · \(done)/\(plan.count)")
                        .chromeText()
                        .foregroundStyle(colors.accent)
                        .fixedSize()
                    if !expanded, let current {
                        Text(current.content)
                            .font(.footnote)
                            .foregroundStyle(colors.textMuted)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.up")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(colors.gray)
                        .rotationEffect(.degrees(expanded ? 180 : 0))
                }
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Plan, \(done) of \(plan.count) done")
            .accessibilityHint(expanded ? "Collapse" : "Expand")
            if expanded {
                PixelDivider()
                ChatCappedScroll(maxHeight: 240) { entries }
            }
        }
        .background(colors.bg2)
        .pixelRing(colors.surface2)
    }

    private var entries: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(plan.enumerated()), id: \.offset) { _, entry in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    marker(entry.status)
                    Text(entry.content)
                        .font(.footnote)
                        .foregroundStyle(entry.status == "completed" ? colors.textMuted : colors.text)
                        .strikethrough(entry.status == "completed", color: colors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .accessibilityElement(children: .combine)
                .accessibilityValue(ChatPlanBar.statusLabel(entry.status))
            }
        }
        .padding(12)
    }

    @ViewBuilder
    private func marker(_ status: String) -> some View {
        switch status {
        case "completed":
            Image(systemName: "checkmark.square.fill").foregroundStyle(colors.textMuted)
        case "in_progress":
            Image(systemName: "square.lefthalf.filled").foregroundStyle(colors.accent)
        default:
            Image(systemName: "square").foregroundStyle(colors.gray)
        }
    }

    static func statusLabel(_ status: String) -> String {
        switch status {
        case "completed": return "Done"
        case "in_progress": return "In progress"
        default: return "Pending"
        }
    }
}

/// Messages waiting for the running turn to end, each removable.
struct ChatQueueList: View {
    let model: ChatSessionModel

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(spacing: 6) {
            ForEach(model.state.queue) { queued in
                HStack(spacing: 8) {
                    PixelChip("Queued", .status(.muted))
                    Text(queued.text.isEmpty ? "\(queued.images) image(s)" : queued.text)
                        .font(.footnote)
                        .foregroundStyle(colors.text)
                        .lineLimit(1)
                    if queued.images > 0, !queued.text.isEmpty {
                        Image(systemName: "photo")
                            .font(.caption)
                            .foregroundStyle(colors.textMuted)
                    }
                    Spacer(minLength: 4)
                    if model.dequeuing.contains(queued.id) {
                        PixelLoadingBlock(width: 7, height: 12)
                            .frame(width: 44, height: 44)
                    } else {
                        if model.canSteer {
                            Button {
                                model.steer(queued)
                            } label: {
                                Image(systemName: "arrow.up")
                                    .font(.footnote.weight(.semibold))
                                    .foregroundStyle(colors.accent)
                                    .frame(width: 44, height: 44)
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .disabled(!model.isLive)
                            .accessibilityLabel("Send now")
                        }
                        Button {
                            model.dequeue(queued)
                        } label: {
                            Image(systemName: "xmark")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(colors.textMuted)
                                .frame(width: 44, height: 44)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .disabled(!model.isLive)
                        .accessibilityLabel("Remove queued message")
                    }
                }
                .padding(.leading, 10)
                .background(colors.bg2)
                .pixelRing(colors.surface2)
            }
        }
    }
}

enum ChatConfigCategory {
    static let chips = ["mode", "model", "thought_level"]

    /// The first select option of each chip category, in chip order.
    static func chipOptions(_ config: [ChatConfigOption]) -> [ChatConfigOption] {
        chips.compactMap { category in
            config.first { $0.type == "select" && $0.category == category }
        }
    }

    /// Everything not shown as a chip, in the agent's order.
    static func otherOptions(_ config: [ChatConfigOption]) -> [ChatConfigOption] {
        let chipIds = Set(chipOptions(config).map(\.id))
        return config.filter { $0.isKnownType && !chipIds.contains($0.id) }
    }

    static func icon(_ option: ChatConfigOption) -> String {
        switch option.category {
        case "mode": return "shield"
        case "model": return "cpu"
        case "thought_level": return "brain"
        default: return "slider.horizontal.3"
        }
    }

    /// The mode chip hints at how much the agent may do unasked.
    static func modeTone(_ value: String) -> PixelTone {
        let id = value.lowercased()
        if id.contains("plan") { return .accent }
        if id.contains("bypass") || id.contains("yolo") { return .danger }
        if id.contains("accept") || id.contains("auto") { return .warning }
        return .muted
    }
}

/// One tappable chip: icon and current value.
struct ChatConfigChip: View {
    let icon: String
    let text: String
    let tone: PixelTone
    let accessibilityLabel: String
    let action: () -> Void

    @Environment(\.mobaideColors) private var colors
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.caption)
                if !text.isEmpty {
                    Text(text)
                        .chromeText()
                        .lineLimit(1)
                }
            }
            .foregroundStyle(isEnabled ? (tone == .muted ? colors.text : colors.color(tone)) : colors.gray)
            .padding(EdgeInsets(top: 6, leading: 10, bottom: 6, trailing: 10))
            .background(colors.surface)
            .pixelRing(isEnabled ? colors.ring(tone) : colors.rule)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityLabel)
    }
}

/// A select option's choices, grouped by `group`, the current one checked.
struct ChatConfigPickerSheet: View {
    let option: ChatConfigOption
    let current: ChatConfigValue
    let onPick: (ChatConfigChoice) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors

    private var groups: [(name: String?, choices: [ChatConfigChoice])] {
        var order: [String?] = []
        var buckets: [String?: [ChatConfigChoice]] = [:]
        for choice in option.options {
            if buckets[choice.group] == nil { order.append(choice.group) }
            buckets[choice.group, default: []].append(choice)
        }
        return order.map { ($0, buckets[$0] ?? []) }
    }

    var body: some View {
        NavigationStack {
            List {
                if let description = option.description {
                    Text(description)
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                        .pixelListRow()
                }
                ForEach(Array(groups.enumerated()), id: \.offset) { _, group in
                    Section {
                        ForEach(group.choices) { choice in
                            ChatChoiceRow(choice: choice, selected: current == .string(choice.value)) {
                                onPick(choice)
                                dismiss()
                            }
                        }
                    } header: {
                        if let name = group.name {
                            PixelSectionHeader(name)
                        }
                    }
                }
            }
            .pixelList()
            .navigationTitle(option.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .pixelSheet()
        .presentationDetents([.medium, .large])
    }
}

private struct ChatChoiceRow: View {
    let choice: ChatConfigChoice
    let selected: Bool
    let action: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(choice.name)
                        .font(.body.weight(selected ? .semibold : .regular))
                        .foregroundStyle(colors.text)
                    if let description = choice.description {
                        Text(description)
                            .font(.caption)
                            .foregroundStyle(colors.textMuted)
                    }
                }
                Spacer(minLength: 8)
                if selected {
                    Image(systemName: "checkmark")
                        .foregroundStyle(colors.accent)
                }
            }
            .frame(minHeight: 32)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .pixelCard(selected: selected, insets: EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12))
        .pixelListRow()
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// Every option that is not a chip: selects as pickers, booleans as toggles.
struct ChatSettingsSheet: View {
    let model: ChatSessionModel

    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors
    @State private var picking: ChatConfigOption?

    var body: some View {
        NavigationStack {
            List {
                ForEach(ChatConfigCategory.otherOptions(model.state.config)) { option in
                    row(option)
                        .pixelCard(insets: EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12))
                        .pixelListRow()
                }
            }
            .pixelList()
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
            .sheet(item: $picking) { option in
                ChatConfigPickerSheet(option: option, current: model.configValue(option)) { choice in
                    model.setConfig(option, to: .string(choice.value))
                }
            }
        }
        .pixelSheet()
        .presentationDetents([.medium, .large])
    }

    @ViewBuilder
    private func row(_ option: ChatConfigOption) -> some View {
        if option.type == "boolean" {
            Toggle(isOn: Binding(
                get: { model.configValue(option) == .bool(true) },
                set: { model.setConfig(option, to: .bool($0)) }
            )) {
                optionLabel(option, value: nil)
            }
            .disabled(!model.canAct)
        } else {
            Button {
                picking = option
            } label: {
                HStack {
                    optionLabel(option, value: choiceName(option))
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(colors.gray)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!model.canAct)
        }
    }

    private func choiceName(_ option: ChatConfigOption) -> String? {
        guard case .string(let value) = model.configValue(option) else { return nil }
        return option.options.first { $0.value == value }?.name ?? value
    }

    private func optionLabel(_ option: ChatConfigOption, value: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(option.name)
                .foregroundStyle(colors.text)
            if let value {
                Text(value)
                    .font(.caption)
                    .foregroundStyle(colors.accent)
            }
            if let description = option.description {
                Text(description)
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
        }
    }
}

/// Context-window use as a small bar and a percent; tap for the numbers.
struct ChatUsageIndicator: View {
    let usage: ChatUsage

    @Environment(\.mobaideColors) private var colors
    @State private var showingDetails = false

    private var fraction: Double { usage.fraction ?? 0 }

    private var percent: Int { Int((fraction * 100).rounded()) }

    private var tone: PixelTone {
        if fraction >= 0.95 { return .danger }
        if fraction >= 0.8 { return .warning }
        return .accent
    }

    var body: some View {
        Button {
            showingDetails = true
        } label: {
            HStack(spacing: 4) {
                ZStack(alignment: .leading) {
                    Rectangle().fill(colors.surface2)
                    Rectangle()
                        .fill(colors.color(tone))
                        .frame(width: 26 * fraction)
                }
                .frame(width: 26, height: 8)
                .pixelRing(colors.gray, width: 1)
                Text("\(percent)%")
                    .font(.caption2.monospacedDigit().weight(.semibold))
                    .foregroundStyle(colors.text)
                    .lineLimit(1)
                    .fixedSize()
                    .dynamicTypeSize(...DynamicTypeSize.xxLarge)
            }
            .padding(.horizontal, 4)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Context used")
        .accessibilityValue("\(percent) percent")
        .popover(isPresented: $showingDetails) {
            VStack(alignment: .leading, spacing: 6) {
                Text("\(Self.tokens(usage.used)) of \(Self.tokens(usage.size)) tokens (\(percent) %)")
                    .font(.footnote)
                    .foregroundStyle(colors.text)
                if let amount = usage.costAmount {
                    Text("≈ \(Self.cost(amount, currency: usage.costCurrency))")
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
            }
            .padding(14)
            .presentationCompactAdaptation(.popover)
            .presentationBackground(colors.bg2)
        }
    }

    static func tokens(_ value: Double) -> String {
        Int(value).formatted(.number.grouping(.automatic))
    }

    static func cost(_ amount: Double, currency: String?) -> String {
        let code = currency ?? "USD"
        if code == "USD" {
            return String(format: "$%.2f", amount)
        }
        return amount.formatted(.currency(code: code))
    }
}
