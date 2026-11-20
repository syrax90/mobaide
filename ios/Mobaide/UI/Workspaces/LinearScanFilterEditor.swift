import SwiftUI

/// Chip and menu options of the Linear Auto Scan filter: the labels and states
/// Linear reported, then every selected value Linear did not report, so a
/// saved choice stays visible and removable. Order-stable, blanks dropped.
enum LinearFilterOptions {
    static func labels(discovered: [LinearLabelDto], selected: Set<String>, extra: [String]) -> [String] {
        var seen = Set<String>()
        var result: [String] = []
        for value in discovered.map(\.name) + selected.sorted() + extra where !value.isEmpty {
            if seen.insert(value).inserted { result.append(value) }
        }
        return result
    }

    static func states(discovered: [LinearStateDto], selected: Set<String>, extra: [String]) -> [LinearStateDto] {
        var seen = Set<String>()
        var result: [LinearStateDto] = []
        let extras = (selected.sorted() + extra).map { LinearStateDto(name: $0) }
        for state in discovered + extras where !state.name.isEmpty {
            if seen.insert(state.name).inserted { result.append(state) }
        }
        return result
    }
}

/// The rows of the Linear Auto Scan filter: trigger labels and trigger states
/// (multi-select chips), then the active state, done state and removal label.
/// Shared by the workspace form (agents without `capabilities.autoscan`) and
/// the New Session wizard; the caller supplies the enclosing `Section`.
struct LinearScanFilterEditor: View {
    @Binding var triggerLabels: Set<String>
    @Binding var triggerStates: Set<String>
    @Binding var activeState: String
    @Binding var doneState: String
    @Binding var removalLabel: String
    let labelOptions: [String]
    let stateOptions: [LinearStateDto]
    let loading: Bool
    let error: String?
    var showsErrors = false
    let onRetry: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        if loading {
            PixelLoadingBlock()
        }
        if let error {
            HStack {
                Text(error).font(.caption).foregroundStyle(colors.danger)
                Spacer()
                Button("Retry", action: onRetry)
                    .buttonStyle(.pixelSmall)
            }
        }

        chipGroup(
            title: "Trigger labels",
            error: showsErrors && triggerLabels.isEmpty ? "Select at least one trigger label" : nil
        ) {
            ForEach(labelOptions, id: \.self) { label in
                SelectableChip(text: label, isSelected: triggerLabels.contains(label)) {
                    Self.toggle(&triggerLabels, label)
                }
            }
        }
        chipGroup(
            title: "Trigger states",
            error: showsErrors && triggerStates.isEmpty ? "Select at least one trigger state" : nil
        ) {
            ForEach(stateOptions, id: \.name) { state in
                SelectableChip(
                    text: state.name,
                    glyph: Self.stateGlyph(state.type),
                    isSelected: triggerStates.contains(state.name)
                ) {
                    Self.toggle(&triggerStates, state.name)
                }
            }
        }

        singleSelect("Active state", selection: $activeState, options: stateOptions.map(\.name))
        singleSelect("Done state", selection: $doneState, options: stateOptions.map(\.name))
        singleSelect("Removal label", selection: $removalLabel, options: labelOptions)
    }

    @ViewBuilder
    private func chipGroup(title: String, error: String?, @ViewBuilder chips: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).sectionHeading()
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) { chips() }
            }
            if let error {
                Text(error).font(.caption).foregroundStyle(colors.danger)
            }
        }
    }

    private func singleSelect(_ label: String, selection: Binding<String>, options: [String]) -> some View {
        Menu {
            ForEach(options, id: \.self) { option in
                Button(option) { selection.wrappedValue = option }
            }
        } label: {
            LabeledContent(label, value: selection.wrappedValue.isEmpty ? "Select" : selection.wrappedValue)
        }
    }

    private static func toggle(_ set: inout Set<String>, _ value: String) {
        if set.contains(value) { set.remove(value) } else { set.insert(value) }
    }

    private static func stateGlyph(_ type: String) -> String {
        switch type {
        case "backlog": return "◴"
        case "unstarted": return "○"
        case "started": return "◐"
        case "completed": return "●"
        case "canceled": return "⊘"
        case "triage": return "△"
        default: return "·"
        }
    }
}

private struct SelectableChip: View {
    let text: String
    var glyph: String?
    let isSelected: Bool
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            PixelChip(glyph.map { "\($0) \(text)" } ?? text, .verbatim(checked: isSelected))
                .padding(.bottom, 2)
        }
        .buttonStyle(.plain)
        .transaction { $0.animation = nil }
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }
}
