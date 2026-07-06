import SwiftUI

@main
struct MobaideApp: App {
    @State private var container = AppContainer()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(container.router)
                .environment(container.authStateModel)
                .environment(\.appContainer, container)
                .mobaideTheme()
                .task {
                    await container.start()
                }
        }
    }
}
