import XCTest
@testable import Mobaide

/// The packing math behind `ChipFlowLayout`. The shortcut picker's key chips
/// depend on it wrapping rather than squeezing: a uniform column width cannot
/// fit both "A" and "Backspace", which is what tail-truncated the F10–F12 and
/// Tab/Enter/Space/Backspace labels under the old `LazyVGrid`.
final class ChipFlowLayoutTests: XCTestCase {

    private func pack(
        _ sizes: [CGSize],
        maxWidth: CGFloat,
        horizontalSpacing: CGFloat = 6,
        verticalSpacing: CGFloat = 6
    ) -> (frames: [CGRect], size: CGSize) {
        ChipFlowPacking.frames(
            sizes: sizes,
            maxWidth: maxWidth,
            horizontalSpacing: horizontalSpacing,
            verticalSpacing: verticalSpacing
        )
    }

    func testEmptyInputProducesNoFramesAndZeroSize() {
        let result = pack([], maxWidth: 300)
        XCTAssertTrue(result.frames.isEmpty)
        XCTAssertEqual(result.size, .zero)
    }

    func testItemsThatFitStayOnOneRowSeparatedByHorizontalSpacing() {
        let result = pack(
            [CGSize(width: 40, height: 30), CGSize(width: 50, height: 30), CGSize(width: 60, height: 30)],
            maxWidth: 300
        )
        XCTAssertEqual(result.frames.map(\.minY), [0, 0, 0])
        XCTAssertEqual(result.frames.map(\.minX), [0, 46, 102])
        XCTAssertEqual(result.size, CGSize(width: 162, height: 30))
    }

    func testOverflowWrapsToNextRowAndResetsX() {
        let result = pack(
            [CGSize(width: 60, height: 30), CGSize(width: 60, height: 30), CGSize(width: 60, height: 30)],
            maxWidth: 130
        )
        XCTAssertEqual(result.frames.map(\.minX), [0, 66, 0])
        XCTAssertEqual(
            result.frames.map(\.minY),
            [0, 0, 36],
            "the third chip starts a new row at firstRowHeight + verticalSpacing"
        )
        XCTAssertEqual(result.size, CGSize(width: 126, height: 66))
    }

    func testWideItemAfterNarrowOnesWrapsInsteadOfBeingSqueezed() {
        // F10, F11, F12 then Backspace: the long chip keeps its full width.
        let narrow = CGSize(width: 48, height: 30)
        let wide = CGSize(width: 96, height: 30)
        let result = pack([narrow, narrow, narrow, wide], maxWidth: 200)
        XCTAssertEqual(result.frames.map(\.minX), [0, 54, 108, 0])
        XCTAssertEqual(result.frames.map(\.minY), [0, 0, 0, 36])
        XCTAssertEqual(result.frames[3].width, 96, "a wrapped chip is never narrowed to fit a column")
    }

    func testItemWiderThanMaxWidthTakesItsOwnRowWithoutLooping() {
        let result = pack(
            [CGSize(width: 40, height: 30), CGSize(width: 500, height: 30), CGSize(width: 40, height: 30)],
            maxWidth: 100
        )
        XCTAssertEqual(result.frames.count, 3)
        XCTAssertEqual(result.frames.map(\.minX), [0, 0, 0])
        XCTAssertEqual(result.frames.map(\.minY), [0, 36, 72])
        XCTAssertEqual(result.size.width, 500, "the reported width covers the oversized child")
    }

    func testRowAdvancesByTallestItemInThatRow() {
        let result = pack(
            [CGSize(width: 40, height: 30), CGSize(width: 40, height: 50), CGSize(width: 40, height: 20)],
            maxWidth: 100
        )
        XCTAssertEqual(result.frames.map(\.minY), [0, 0, 56])
        XCTAssertEqual(result.size.height, 76)
    }

    func testReportedSizeEqualsUnionOfFrames() {
        let sizes = (0..<17).map { CGSize(width: 30 + CGFloat($0 % 5) * 14, height: 24 + CGFloat($0 % 3) * 6) }
        let result = pack(sizes, maxWidth: 210)
        let union = result.frames.reduce(CGRect.null) { $0.union($1) }
        XCTAssertEqual(result.size.width, union.maxX, "sizeThatFits must match what placeSubviews lays out")
        XCTAssertEqual(result.size.height, union.maxY)
        XCTAssertLessThanOrEqual(union.maxX, 210)
    }

    func testUsableWidthTreatsMissingOrDegenerateProposalsAsUnbounded() {
        XCTAssertEqual(ChipFlowPacking.usableWidth(320), 320)
        XCTAssertEqual(ChipFlowPacking.usableWidth(nil), .infinity)
        XCTAssertEqual(ChipFlowPacking.usableWidth(0), .infinity)
        XCTAssertEqual(ChipFlowPacking.usableWidth(-10), .infinity)
        XCTAssertEqual(ChipFlowPacking.usableWidth(.infinity), .infinity)
    }

    func testUnboundedWidthKeepsEverythingOnOneRow() {
        let sizes = Array(repeating: CGSize(width: 48, height: 30), count: 26)
        let result = pack(sizes, maxWidth: .infinity)
        XCTAssertEqual(Set(result.frames.map(\.minY)), [0])
        XCTAssertEqual(result.size.height, 30)
    }
}
