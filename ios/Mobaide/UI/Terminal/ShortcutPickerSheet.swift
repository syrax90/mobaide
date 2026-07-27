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
            Divider()
            footer(chord: chord, encoding: encoding, savedMatch: savedMatch)
        }
        .padding(.horizontal, 16)
        .padding(.top, 16)
        // The sheet is its own presentation layer: the terminal screen's toast
        // overlay renders behind it, so failures triggered from here must
        // surface on the sheet itself.
        .toast($model.shortcutMessage)
        .renameShortcutAlert(target: $renameTarget) { shortcut, name in
            model.renameShortcut(id: shortcut.id, name: name)
        }
    }

    private var header: some View {
        HStack {
            Text("Keyboard shortcut")
                .font(.headline)
            Spacer()
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
            }
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
                    .buttonStyle(.bordered)
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
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 44), spacing: 6)], spacing: 6) {
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
                        .font(.subheadline.monospaced())
                    Text(ShortcutPickerLogic.encodingCaption(encoding))
                        .font(.caption)
                        .foregroundStyle(colors.onSurfaceVariant)
                } else {
                    Text("Pick a key")
                        .font(.caption)
                        .foregroundStyle(colors.onSurfaceVariant)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            saveToggle(chord: chord, savedMatch: savedMatch)
        }
        if encoding?.usesCsiU == true {
            Text("CSI-u sequence — may not work in every CLI")
                .font(.caption)
                .foregroundStyle(colors.tertiary)
        }
        if let savedMatch {
            // Doubles as the save confirmation and the already-pinned marker:
            // it shows for any composed chord that is already a button, not
            // only right after a save.
            HStack(spacing: 6) {
                Image(systemName: "checkmark")
                    .font(.caption)
                    .foregroundStyle(colors.onSurfaceVariant)
                Text("Saved as \"\(savedMatch.label)\"")
                    .font(.caption)
                    .foregroundStyle(colors.onSurfaceVariant)
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
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
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
        return Button {
            if let savedMatch {
                model.removeShortcut(id: savedMatch.id)
            } else if let chord {
                model.saveShortcut(chord)
            }
        } label: {
            Label(saved ? "Saved" : "Save", systemImage: saved ? "bookmark.fill" : "bookmark")
        }
        .buttonStyle(.bordered)
        .tint(saved ? colors.primary : nil)
        .disabled(chord == nil)
    }

    private func sectionHeader(_ label: String) -> some View {
        Text(label)
            .font(.caption)
            .foregroundStyle(colors.onSurfaceVariant)
    }

    private func chipLabel(_ text: String) -> some View {
        Text(text)
            .font(.footnote.monospaced())
            .lineLimit(1)
    }

    private func chipGrid<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 110), spacing: 6)], alignment: .leading, spacing: 6) {
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
            button.buttonStyle(.borderedProminent)
        } else {
            button.buttonStyle(.bordered)
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
                    .font(.footnote.monospaced())
                    .lineLimit(1)
                    .foregroundStyle(shortcut.risky ? colors.tertiary : colors.onSurfaceVariant)
                    .opacity(enabled ? 1 : 0.38)
            }
            .buttonStyle(.plain)
            Button(action: onRemove) {
                Image(systemName: "xmark")
                    .font(.caption2)
                    .foregroundStyle(colors.onSurfaceVariant)
                    .frame(width: 28, height: 28)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(shortcut.label)")
        }
        .padding(.leading, 10)
        .padding(.vertical, 2)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(colors.outline, lineWidth: 1)
        )
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

    static func clampName(_ name: String) -> String {
        String(name.prefix(SavedShortcutUi.maxNameLength))
    }
}
