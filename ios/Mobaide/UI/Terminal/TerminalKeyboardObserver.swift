import SwiftUI
import UIKit

/// Tracks the keyboard's window overlap for the terminal screen's
/// pan-don't-resize layout. The screen opts out of SwiftUI's automatic
/// keyboard avoidance and instead lifts its column by `transition.lift`
/// (Android `ImeFloatingPan` parity), so the terminal's allocated size never
/// changes with the keyboard — no grid resize, no PTY `r` frame.
@MainActor
@Observable
final class TerminalKeyboardObserver {
    struct Transition: Equatable {
        var lift: CGFloat = 0
        var duration: TimeInterval = 0.25
    }

    private(set) var transition = Transition()

    /// Fired after a will-hide transition update so the screen can reconcile
    /// terminal first-responder state for system-initiated dismissals that
    /// bypass the responder overrides.
    var onKeyboardWillHide: (() -> Void)?

    /// Test seam for window geometry.
    var windowProvider: @MainActor () -> UIWindow? = {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)
    }

    /// The keyboard's real curve, built from the shared spring scalars so the
    /// SwiftUI column offset and the wrapper's UIKit counter-pan animator
    /// cancel exactly when the net movement should be zero.
    static let animation = Animation.interpolatingSpring(
        mass: TerminalKeyboardPan.springMass,
        stiffness: TerminalKeyboardPan.springStiffness,
        damping: TerminalKeyboardPan.springDamping,
        initialVelocity: 0
    )

    private var observers: [NSObjectProtocol] = []

    func start() {
        guard observers.isEmpty else { return }
        let center = NotificationCenter.default
        observers.append(center.addObserver(
            forName: UIResponder.keyboardWillChangeFrameNotification, object: nil, queue: .main
        ) { [weak self] note in
            guard let frame = Self.frame(from: note) else { return }
            MainActor.assumeIsolated { self?.handle(frame) }
        })
        observers.append(center.addObserver(
            forName: UIResponder.keyboardWillHideNotification, object: nil, queue: .main
        ) { [weak self] note in
            guard let frame = Self.frame(from: note) else { return }
            MainActor.assumeIsolated {
                self?.handle(frame)
                self?.onKeyboardWillHide?()
            }
        })
    }

    /// Stops observing and snaps the lift back unanimated — the screen calls
    /// this on disappear so no stale offset survives a later return.
    func stop() {
        for token in observers {
            NotificationCenter.default.removeObserver(token)
        }
        observers.removeAll()
        transition = Transition(lift: 0, duration: 0)
    }

    isolated deinit {
        stop()
    }

    /// Notification glue extracted for direct testing.
    func apply(endFrameInWindow: CGRect, windowBounds: CGRect, bottomSafeInset: CGFloat, duration: TimeInterval) {
        let lift = TerminalKeyboardPan.lift(
            keyboardEndFrame: endFrameInWindow,
            windowBounds: windowBounds,
            bottomSafeInset: bottomSafeInset
        )
        let next = Transition(lift: lift, duration: duration)
        guard next != transition else { return }
        withAnimation(Self.animation) {
            transition = next
        }
    }

    /// Sendable snapshot of the notification fields `handle` needs, extracted
    /// up front so the `NSValue`-boxed `Notification` never crosses into the
    /// `MainActor.assumeIsolated` closure.
    private struct KeyboardFrame: Sendable {
        var endFrame: CGRect
        var duration: TimeInterval
    }

    private nonisolated static func frame(from note: Notification) -> KeyboardFrame? {
        guard let endValue = note.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? NSValue else { return nil }
        let duration = note.userInfo?[UIResponder.keyboardAnimationDurationUserInfoKey] as? TimeInterval ?? 0.25
        return KeyboardFrame(endFrame: endValue.cgRectValue, duration: duration)
    }

    private func handle(_ frame: KeyboardFrame) {
        guard let window = windowProvider() else { return }
        // Convert from screen space — the window may not fill the screen
        // (Split View / Stage Manager).
        let endFrame = window.convert(frame.endFrame, from: window.screen.coordinateSpace)
        apply(
            endFrameInWindow: endFrame,
            windowBounds: window.bounds,
            bottomSafeInset: window.safeAreaInsets.bottom,
            duration: frame.duration
        )
    }
}
