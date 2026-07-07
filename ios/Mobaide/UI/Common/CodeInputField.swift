import SwiftUI

/// Mirror of Android `CodeInputField`: segmented OTP entry — one cell per
/// digit backed by a single invisible text field, append-only editing
/// (backspace trims the last digit).
struct CodeInputField: View {
    @Binding var code: String
    var length: Int = 6
    var enabled: Bool = true
    var isError: Bool = false
    var autoFocus: Bool = true
    var onSubmit: () -> Void = {}

    @FocusState private var focused: Bool

    var body: some View {
        ZStack {
            TextField("", text: $code)
                .keyboardType(.numberPad)
                .textContentType(.oneTimeCode)
                .focused($focused)
                .opacity(0.02)
                .disabled(!enabled)
                .onSubmit(onSubmit)
                .accessibilityLabel("Verification code")
            HStack(spacing: 8) {
                ForEach(0..<length, id: \.self) { index in
                    CodeCell(
                        character: character(at: index),
                        isActive: focused && code.count == index,
                        isError: isError,
                        enabled: enabled
                    )
                }
            }
            .contentShape(Rectangle())
            .onTapGesture {
                if enabled { focused = true }
            }
            .allowsHitTesting(enabled)
        }
        .frame(height: 56)
        .onChange(of: code) { _, newValue in
            let filtered = String(newValue.filter(\.isNumber).prefix(length))
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
