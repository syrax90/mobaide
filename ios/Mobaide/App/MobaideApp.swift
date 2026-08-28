import GoogleSignIn
import SwiftUI

@main
struct MobaideApp: App {
    @State private var container = AppContainer()
    // APNs token callbacks only reach a UIApplicationDelegate.
    @UIApplicationDelegateAdaptor(PushAppDelegate.self) private var pushDelegate
    @Environment(\.scenePhase) private var scenePhase

    init() {
        // Before the first NavigationStack exists: the appearance proxy only
        // reaches bars created after it is set.
        PixelNavigationBarAppearance.install()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .modifier(ThemePreferenceModifier(themeStore: container.themeStore))
                .environment(container.router)
                .environment(container.authStateModel)
                .environment(container.deepLinkCoordinator)
                .environment(\.appContainer, container)
                .mobaideTheme()
                .task {
                    container.attachPushDelegate(pushDelegate)
                    await container.start()
                }
                .onChange(of: scenePhase) { _, phase in
                    // Background grace (keep the events socket alive ~30 s) +
                    // foreground reconcile of missed attention (iOS-09).
                    container.appLifecycleController.handle(phase)
                    // Re-probe the version gate on foreground so a server
                    // upgraded while backgrounded still triggers the takeover
                    // (Android `MainActivity.onResume` parity). The initial
                    // `.active` is not a change, so launch is covered by
                    // `container.start()` alone.
                    if phase == .active {
                        Task { await container.versionGate.probe() }
                    }
                }
                .onOpenURL { url in
                    // Completes the Google sign-in round trip (reversed
                    // client-id URL scheme).
                    _ = GIDSignIn.sharedInstance.handle(url)
                }
        }
    }
}
