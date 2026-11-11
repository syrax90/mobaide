import SwiftUI
import XCTest
@testable import Mobaide

/// The edge pan's open decision: a third of the way, or a flick.
@MainActor
final class SideDrawerTests: XCTestCase {
    /// The decision is static; the panel type is irrelevant to it.
    private typealias Drawer = SideDrawer<EmptyView>

    func testADragPastAThirdOfTheWidthOpens() {
        XCTAssertTrue(Drawer.shouldOpen(translation: 101, velocity: 0, width: 300))
        XCTAssertFalse(Drawer.shouldOpen(translation: 100, velocity: 0, width: 300), "a third exactly is not past it")
        XCTAssertFalse(Drawer.shouldOpen(translation: 60, velocity: 0, width: 300))
    }

    func testAFlickOpensFromAShortDrag() {
        XCTAssertTrue(Drawer.shouldOpen(translation: 21, velocity: 301, width: 300))
        XCTAssertFalse(Drawer.shouldOpen(translation: 20, velocity: 1000, width: 300), "a twitch at the edge is not a flick")
        XCTAssertFalse(Drawer.shouldOpen(translation: 60, velocity: 300, width: 300), "the velocity bar is exclusive")
    }

    func testAnOpenDecisionIgnoresTheReturnVelocityOnceFarEnough() {
        // The finger came back a little at the end of a long pull; the drawer
        // is already most of the way out and must not snap shut.
        XCTAssertTrue(Drawer.shouldOpen(translation: 250, velocity: -400, width: 300))
    }
}
