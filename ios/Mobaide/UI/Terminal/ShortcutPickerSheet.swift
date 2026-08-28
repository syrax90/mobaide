import SwiftUI

/// Chord picker for arbitrary keyboard shortcuts (Android
/// `ShortcutPickerSheet`): Ctrl/Alt/Shift toggles plus a base-key grid, with
/// per-session-kind recents on top for one-tap resend and the kind's
/// server-synced saved shortcuts below them (tap = send, ✕ = remove). Sending
/// dismisses the sheet; saving keeps it open so the new chip appears in place.
///
/// Layout is footer-first: the key grids scroll inside the flexible middle
/// child while the header and the footer (preview + Save toggle + Send) keep
/// their ideal heights, so Send stays reachable on short/landscape screens.
struct ShortcutPickerSheet: View {
    @Bindable var model: TerminalSessionModel

    @Environment(\.dismiss) private var dismiss
    @Environment(\.mobaideColors) private var colors

    @State private var ctrl = false
    @State private var alt = false
    @State private var shift = false
    @State private var selectedKey: String?
    @State private var renameTarget: SavedShortcutUi?

    private var chord: KeyChord? {
        selectedKey.map { KeyChord(ctrl: ctrl, alt: alt, shift: shift, key: $0) }
    }

    var body: some View {
        let chord = self.chord
        let encoding = chord.flatMap(KeyChordEncoder.encode)
        let savedMatch = ShortcutPickerLogic.savedMatch(chord: chord, in: model.savedShortcutsForKind)

        VStack(alignment: .leading, spacing: 12) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    recentsSection
                    savedSection
                    modifiersSection
                    keySection("Letters", keys: KeyChordKeys.letters)
                    keySection("Numbers", keys: KeyChordKeys.digits)
                    keySection("Function & special", keys: KeyChordKeys.functionKeys + KeyChordKeys.specials)
                }
            }
            PixelDivider()
            footer(chord: chord, encoding: encoding, savedMatch: savedMatch)
        }
        .padding(.horizontal, 16)
        .padding(.top, 16)
        .pixelSheet()
        // The sheet is its own presentation layer: the terminal screen's toast
        // overlay renders behind it, so failures triggered from here must
        // surface on the sheet itself.
        .toast($model.terminalMessage)
        .renameShortcutAlert(target: $renameTarget) { shortcut, name in
            model.renameShortcut(id: shortcut.id, name: name)
        }
    }

    private var header: some View {
        HStack {
            Text("Keyboard shortcut")
                .font(MobaideTypography.dialogTitle)
                .foregroundStyle(colors.accent)
            Spacer()
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .foregroundStyle(colors.accent)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close")
        }
    }

    @ViewBuilder
    private var recentsSection: some View {
        if !model.shortcutRecents.isEmpty {
            sectionHeader("Recents")
            chipGrid {
                ForEach(model.shortcutRecents, id: \.token) { recent in
                    Button {
                        model.sendChord(recent)
                        dismiss()
                    } label: {
                        chipLabel(recent.display)
                    }
                    .buttonStyle(.pixelSmall)
                    .disabled(!model.canSendTerminalBytes)
                }
            }
        }
    }

    @ViewBuilder
    private var savedSection: some View {
        if !model.savedShortcutsForKind.isEmpty {
            sectionHeader("Saved")
            chipGrid {
                ForEach(model.savedShortcutsForKind) { saved in
                    SavedShortcutChip(
                        shortcut: saved,
                        enabled: model.canSendTerminalBytes,
                        onSend: {
                            model.sendChord(saved.chord)
                            dismiss()
                        },
                        onRemove: { model.removeShortcut(id: saved.id) }
                    )
                }
            }
        }
    }

    private var modifiersSection: some View {
        let selectedCount = [ctrl, alt, shift].filter { $0 }.count
        return Group {
            sectionHeader("Modifiers")
            HStack(spacing: 6) {
                modifierChip("Ctrl", isOn: $ctrl, selectedCount: selectedCount)
                modifierChip("Alt", isOn: $alt, selectedCount: selectedCount)
                modifierChip("Shift", isOn: $shift, selectedCount: selectedCount)
            }
        }
    }

    /// Max chord = 2 modifiers + 1 base key: the third, unselected modifier
    /// disables once two are on.
    private func modifierChip(_ label: String, isOn: Binding<Bool>, selectedCount: Int) -> some View {
        toggleChip(
            label,
            selected: isOn.wrappedValue,
            enabled: ShortcutPickerLogic.modifierChipEnabled(
                selected: isOn.wrappedValue,
                selectedCount: selectedCount
            )
        ) {
            isOn.wrappedValue.toggle()
        }
    }

    @ViewBuilder
    private func keySection(_ title: String, keys: [String]) -> some View {
        sectionHeader(title)
        // Flow, not a grid: a uniform column width cannot fit both "A" and
        // "Backspace", and this section mixes them.
        ChipFlowLayout(horizontalSpacing: 6, verticalSpacing: 6) {
            ForEach(keys, id: \.self) { key in
                toggleChip(KeyChordKeys.displayName(key), selected: key == selectedKey, enabled: true) {
                    selectedKey = key == selectedKey ? nil : key
                }
            }
        }
    }

    @ViewBuilder
    private func footer(chord: KeyChord?, encoding: ChordEncoding?, savedMatch: SavedShortcutUi?) -> some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                if let chord, let encoding {
                    Text(chord.display)
                        .font(MobaideTypography.chromeBody)
                        .foregroundStyle(colors.text)
                    Text(ShortcutPickerLogic.encodingCaption(encoding))
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                } else {
                    Text("Pick a key")
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            saveToggle(chord: chord, savedMatch: savedMatch)
        }
        if encoding?.usesCsiU == true {
            Text("CSI-u sequence — may not work in every CLI")
                .font(.caption)
                .foregroundStyle(colors.warning)
        }
        if let savedMatch {
            // Doubles as the save confirmation and the already-pinned marker:
            // it shows for any composed chord that is already a button, not
            // only right after a save.
            HStack(spacing: 6) {
                Image(systemName: "checkmark")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
                Text("Saved as \"\(savedMatch.label)\"")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button(savedMatch.name == nil ? "Name" : "Rename") {
                    renameTarget = savedMatch
                }
                .font(.caption)
            }
        }
        Button {
            if let chord {
                model.sendChord(chord)
                dismiss()
            }
        } label: {
            Text("Send")
        }
        .buttonStyle(.pixelPrimaryBlock)
        .disabled(!ShortcutPickerLogic.sendEnabled(gate: model.canSendTerminalBytes, hasChord: chord != nil))
        .padding(.bottom, 16)
    }

    /// Two-state pin control: "Save" persists the composed chord as a button,
    /// "Saved" unpins it again. Deliberately NOT session-gated — both
    /// directions are server mutations, not PTY sends. No in-flight state on
    /// purpose either: the cache-derived saved-state is the only completion
    /// signal, and double-tap safety lives in the model (duplicate saves
    /// dropped, repeat deletes absorbed).
    private func saveToggle(chord: KeyChord?, savedMatch: SavedShortcutUi?) -> some View {
        let saved = savedMatch != nil
        let button = Button {
            if let savedMatch {
                model.removeShortcut(id: savedMatch.id)
            } else if let chord {
                model.saveShortcut(chord)
            }
        } label: {
            Label(saved ? "Saved" : "Save", systemImage: saved ? "bookmark.fill" : "bookmark")
        }
        .disabled(chord == nil)
        return Group {
            if saved {
                button.buttonStyle(.pixelSmallPrimary)
            } else {
                button.buttonStyle(.pixelSmall)
            }
        }
    }

    private func sectionHeader(_ label: String) -> some View {
        Text(label)
            .sectionHeading()
    }

    private func chipLabel(_ text: String) -> some View {
        Text(text)
            .lineLimit(1)
    }

    private func chipGrid<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        ChipFlowLayout(horizontalSpacing: 6, verticalSpacing: 6) {
            content()
        }
    }

    @ViewBuilder
    private func toggleChip(_ label: String, selected: Bool, enabled: Bool, action: @escaping () -> Void) -> some View {
        let button = Button(action: action) {
            chipLabel(label)
                .frame(minWidth: 24)
        }
        .disabled(!enabled)
        if selected {
            button.buttonStyle(.pixelSmallPrimary)
        } else {
            button.buttonStyle(.pixelSmall)
        }
    }
}

/// A saved shortcut as a picker chip: the label tap resends the chord (inert
/// while the session gate is down — the label dims), the trailing ✕ removes it
/// on the server (never gated: removal must work even when the session is
/// stopped). Label and ✕ are separate buttons, so a send tap can never land
/// on remove.
private struct SavedShortcutChip: View {
    let shortcut: SavedShortcutUi
    let enabled: Bool
    let onSend: () -> Void
    let onRemove: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        HStack(spacing: 2) {
            Button {
                guard enabled else { return }
                onSend()
            } label: {
                Text(shortcut.label)
                    .font(MobaideTypography.chromeBody)
                    .lineLimit(1)
                    .foregroundStyle(shortcut.risky ? colors.warning : colors.text)
                    .opacity(enabled ? 1 : 0.38)
            }
            .buttonStyle(.plain)
            Button(action: onRemove) {
                Image(systemName: "xmark")
                    .font(.caption2)
                    .foregroundStyle(colors.textMuted)
                    .frame(width: 28, height: 28)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(shortcut.label)")
        }
        .padding(.leading, 10)
        .padding(.vertical, 2)
        .background(colors.bg2)
        .pixelRing(colors.rule)
    }
}

/// Pure enable/derivation rules for the picker, extracted for unit tests
/// (Android tests the composables with Robolectric; this suite has no view
/// tests, so the logic lives in statics instead).
enum ShortcutPickerLogic {
    static func modifierChipEnabled(selected: Bool, selectedCount: Int) -> Bool {
        selected || selectedCount < 2
    }

    static func savedMatch(chord: KeyChord?, in saved: [SavedShortcutUi]) -> SavedShortcutUi? {
        guard let chord else { return nil }
        return saved.first { $0.chord == chord }
    }

    static func sendEnabled(gate: Bool, hasChord: Bool) -> Bool {
        gate && hasChord
    }

    static func encodingCaption(_ encoding: ChordEncoding) -> String {
        var text = "Sends: \(encoding.summary)"
        if let same = encoding.sameAsPlainKey {
            text += " — same byte as \(same)"
        }
        return text
    }

    /// Clamp in the server's unit: the 100-char limit counts code points
    /// (Pydantic `max_length` on `str`), not grapheme clusters — a
    /// `String.prefix` clamp would pass emoji/combining-mark names that the
    /// server then 422s. Truncates at a grapheme boundary so no cluster is
    /// split.
    static func clampName(_ name: String) -> String {
        guard name.unicodeScalars.count > SavedShortcutUi.maxNameLength else { return name }
        var scalarCount = 0
        var end = name.startIndex
        for character in name {
            let next = scalarCount + character.unicodeScalars.count
            if next > SavedShortcutUi.maxNameLength { break }
            scalarCount = next
            end = name.index(after: end)
        }
        return String(name[..<end])
    }
}
