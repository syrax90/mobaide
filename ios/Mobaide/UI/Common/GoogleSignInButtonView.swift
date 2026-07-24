import SwiftUI

/// Shared geometry for the provider sign-in buttons so the native
/// `SignInWithAppleButton` and the custom `GoogleSignInButtonView` render at
/// exactly the same height, width, and corner radius.
enum ProviderButton {
    static let height: CGFloat = 44
    static let cornerRadius: CGFloat = 8

    static var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
    }
}

extension View {
    /// Applied to `SignInWithAppleButton` to match the custom Google button.
    /// The native button is not clipped: clipping a `UIViewRepresentable` drops
    /// the `.white` style's fill in dark mode, and its native corner radius
    /// already matches `ProviderButton.cornerRadius` at this height.
    func providerButtonFrame() -> some View {
        frame(maxWidth: .infinity)
            .frame(height: ProviderButton.height)
    }
}

/// Custom "Sign in with Google" button styled to match the adjacent native
/// `SignInWithAppleButton`: identical 44pt height, full width, and corner
/// radius, with mirrored contrast — a dark fill in light mode and a light fill
/// in dark mode — so the two read as a matched pair.
///
/// Replaces the GoogleSignInSwift SDK button, whose fixed internal metrics don't
/// honor an external `.frame()` and so can't be aligned to Apple's. Stays within
/// Google's branding guidelines: approved CTA text, the unaltered multicolor "G"
/// logo, and Google's compliant Dark/Light theme colors. The one deliberate
/// deviation is the system font (matching the Apple button) instead of Google
/// Sans — cross-button consistency is the goal.
struct GoogleSignInButtonView: View {
    /// Mirrors the Apple button's `.signIn` / `.signUp` / `.continue` labels.
    enum Label {
        case signIn, signUp, `continue`

        var text: String {
            switch self {
            case .signIn: return "Sign in with Google"
            case .signUp: return "Sign up with Google"
            case .continue: return "Continue with Google"
            }
        }
    }

    let label: Label
    let action: () -> Void

    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image("GoogleLogo")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: 18, height: 18)
                Text(label.text)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(textColor)
            }
            .frame(maxWidth: .infinity)
            .frame(height: ProviderButton.height)
            .background(fillColor, in: ProviderButton.shape)
            .overlay(ProviderButton.shape.strokeBorder(strokeColor, lineWidth: 1))
            .contentShape(ProviderButton.shape)
        }
        .buttonStyle(.plain)
        .opacity(isEnabled ? 1 : 0.5)
    }

    /// Mirror the Apple button's contrast: dark theme in light mode, light theme
    /// in dark mode.
    private var usesDarkTheme: Bool { colorScheme == .light }

    private var fillColor: Color {
        usesDarkTheme ? Color(hex: 0x131314) : Color(hex: 0xFFFFFF)
    }

    private var textColor: Color {
        usesDarkTheme ? Color(hex: 0xE3E3E3) : Color(hex: 0x1F1F1F)
    }

    private var strokeColor: Color {
        usesDarkTheme ? Color(hex: 0x8E918F) : Color(hex: 0x747775)
    }
}

#Preview("Light") {
    VStack(spacing: 16) {
        GoogleSignInButtonView(label: .signIn) {}
        GoogleSignInButtonView(label: .signUp) {}
        GoogleSignInButtonView(label: .continue) {}
        GoogleSignInButtonView(label: .signIn) {}.disabled(true)
    }
    .padding(24)
    .preferredColorScheme(.light)
}

#Preview("Dark") {
    VStack(spacing: 16) {
        GoogleSignInButtonView(label: .signIn) {}
        GoogleSignInButtonView(label: .signUp) {}
        GoogleSignInButtonView(label: .continue) {}
    }
    .padding(24)
    .preferredColorScheme(.dark)
}
