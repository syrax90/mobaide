import Observation
import SwiftUI

/// Main-actor `@Observable` mirror of `SessionManager.authState` for the
/// SwiftUI root switch. Seeded with the same initial value passed to the
/// SessionManager and then bound to its replay-latest stream, so the first
/// frame renders the correct root and no transition is missed.
@MainActor
@Observable
final class AuthStateModel {
    private(set) var state: AuthState
    private var bindTask: Task<Void, Never>?

    init(initial: AuthState) {
        self.state = initial
    }

    func bind(to sessionManager: SessionManager) {
        guard bindTask == nil else { return }
        let stream = sessionManager.authStates()
        bindTask = Task { [weak self] in
            for await newState in stream {
                self?.state = newState
            }
        }
    }
}
