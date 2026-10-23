import SwiftUI
import UIKit

/// Turns the navigation controller's edge-swipe-to-pop off for the screen it
/// is attached to, so the left edge can belong to that screen's own gesture.
/// The back button is untouched. Restored whenever the screen leaves the top
/// of the stack, including a push over it, and when the view is torn down.
struct InteractivePopGestureDisabler: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> Controller { Controller() }

    func updateUIViewController(_ uiViewController: Controller, context: Context) {}

    static func dismantleUIViewController(_ uiViewController: Controller, coordinator: ()) {
        uiViewController.restore()
    }

    final class Controller: UIViewController {
        private weak var navigation: UINavigationController?
        private var previous: Bool?

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            apply()
        }

        override func viewWillDisappear(_ animated: Bool) {
            super.viewWillDisappear(animated)
            restore()
        }

        private func apply() {
            guard previous == nil,
                  let navigation = navigationController,
                  let gesture = navigation.interactivePopGestureRecognizer
            else { return }
            self.navigation = navigation
            previous = gesture.isEnabled
            gesture.isEnabled = false
        }

        func restore() {
            guard let previous else { return }
            navigation?.interactivePopGestureRecognizer?.isEnabled = previous
            self.previous = nil
        }
    }
}
