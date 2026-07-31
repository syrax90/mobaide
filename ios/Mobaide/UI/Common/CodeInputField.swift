import SwiftUI

/// Mirror of Android `CodeInputField`: segmented OTP entry — one cell per
/// digit backed by a single invisible text field, append-only editing
/// (backspace trims the last digit).
///
/// The cells are decoration only (`allowsHitTesting(false)`) so touches reach
/// the text field, which is what raises the system long-press "Paste" callout.
/// `.numberPad` has no paste key, so the field also carries a keyboard
/// accessory bar with a `PasteButton` — that reads the pasteboard through the
/// system broker, so no "Allow Paste?" prompt appears.
struct CodeInputField: View {
    @Binding var code: String
    var length: Int = 6
    var enabled: Bool = true
    var isError: Bool = false
    var autoFocus: Bool = true
    /// Android contentDescription parity: "Verification code" vs "Reset code".
    var accessibilityLabelText: String = "Verification code"
    var onSubmit: () -> Void = {}

    @FocusState private var focused: Bool

    var body: some View {
        ZStack {
            // The text field lays out one line tall, so it only covers a band
            // across the middle of the row; this backstop keeps the rest of
            // the cells tappable. Only while unfocused: its tap recognizer
            // competes with the field's long-press, which is what raises the
            // system "Paste" callout, and refocusing is the sole job it has.
            if !focused {
                Color.clear
                    .contentShape(Rectangle())
                    .onTapGesture { if enabled { focused = true } }
            }
            TextField("", text: $code)
                .keyboardType(.numberPad)
                .textContentType(.oneTimeCode)
                .focused($focused)
                .opacity(0.02)
                .disabled(!enabled)
                .onSubmit(onSubmit)
                .accessibilityLabel(accessibilityLabelText)
                // Keyboard toolbars are window-scoped, so the *contents* are
                // gated on focus — otherwise this bar also rides above the
                // reset screen's password fields. The group itself must stay
                // unconditional: wrapping `ToolbarItemGroup` in the `if`
                // instead tears it down for good, and it never comes back on a
                // later focus.
                .toolbar {
                    ToolbarItemGroup(placement: .keyboard) {
                        if focused {
                            PasteButton(payloadType: String.self) { strings in
                                guard let raw = strings.first else { return }
                                code = Self.sanitized(raw, length: length)
                            }
                            .labelStyle(.titleAndIcon)
                            Spacer()
                            Button("Done") { focused = false }
                        }
                    }
                }
            HStack(spacing: 8) {
                ForEach(0..<length, id: \.self) { index in
                    CodeCell(
                        character: character(at: index),
                        isActive: focused && index == min(code.count, length - 1),
                        isError: isError,
                        enabled: enabled
                    )
                }
            }
            .allowsHitTesting(false)
        }
        .frame(height: 56)
        .onChange(of: code) { _, newValue in
            let filtered = Self.sanitized(newValue, length: length)
            if filtered != newValue {
                code = filtered
            }
        }
        .onAppear {
            if autoFocus && enabled {
                focused = true
            }
        }
    }

    /// Keeps only ASCII digits and caps at [length]. Non-ASCII numerics
    /// (Arabic-Indic digits, fractions) are dropped — the server accepts
    /// `0-9` only. Stripping everything else is what lets a whole sentence
    /// pasted out of the email ("Your Mobaide code is 123 456") land as a
    /// usable code.
    static func sanitized(_ raw: String, length: Int) -> String {
        String(raw.filter { $0.isASCII && $0.isWholeNumber }.prefix(length))
    }

    private func character(at index: Int) -> String {
        guard index < code.count else { return "" }
        return String(Array(code)[index])
    }
}

private struct CodeCell: View {
    let character: String
    let isActive: Bool
    let isError: Bool
    let enabled: Bool

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Text(character)
            .font(MobaideTypography.otpDigit)
            .frame(maxWidth: .infinity)
            .aspectRatio(0.8, contentMode: .fit)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(borderColor, lineWidth: isActive ? 2 : 1)
            )
            .opacity(enabled ? 1 : 0.5)
    }

    private var borderColor: Color {
        if isError { return colors.error }
        if isActive { return colors.primary }
        if !character.isEmpty { return colors.outline }
        return colors.outlineVariant
    }
}
