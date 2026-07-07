import SwiftUI

extension EnvironmentValues {
    /// Composition root, available to every view. Feature screens
    /// (iOS-03+) pull their dependencies from here when constructing
    /// their models.
    @Entry var appContainer: AppContainer? = nil
}
