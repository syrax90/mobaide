import SwiftUI

/// Wrapping flow layout for chip rows (Android `FlowRow`): each child keeps its
/// intrinsic width and the row breaks when the next child no longer fits, so
/// long labels stay readable instead of being squeezed into a uniform grid
/// column. Children are left-aligned; a uniform look for short chips comes from
/// a `minWidth` frame on the child, not from the layout.
struct ChipFlowLayout: Layout {
    var horizontalSpacing: CGFloat = 6
    var verticalSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        packing(proposal: proposal, subviews: subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let frames = packing(proposal: proposal, subviews: subviews).frames
        for (index, frame) in frames.enumerated() {
            subviews[index].place(
                at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY),
                proposal: ProposedViewSize(frame.size)
            )
        }
    }

    /// Both passes must agree, so they share one measurement: children are
    /// measured against the available width (not `.unspecified`) so an
    /// over-long child clamps to the row instead of overflowing the container.
    private func packing(proposal: ProposedViewSize, subviews: Subviews) -> (frames: [CGRect], size: CGSize) {
        let maxWidth = ChipFlowPacking.usableWidth(proposal.width)
        let childProposal = maxWidth.isFinite
            ? ProposedViewSize(width: maxWidth, height: nil)
            : ProposedViewSize.unspecified
        let sizes = subviews.map { $0.sizeThatFits(childProposal) }
        return ChipFlowPacking.frames(
            sizes: sizes,
            maxWidth: maxWidth,
            horizontalSpacing: horizontalSpacing,
            verticalSpacing: verticalSpacing
        )
    }
}

/// Pure packing math behind `ChipFlowLayout`, extracted so it can be unit
/// tested (this suite has no view tests).
enum ChipFlowPacking {
    /// A missing, non-finite or non-positive proposal means "no width limit":
    /// everything lands on a single row rather than one child per row.
    static func usableWidth(_ proposed: CGFloat?) -> CGFloat {
        guard let proposed, proposed.isFinite, proposed > 0 else { return .infinity }
        return proposed
    }

    static func frames(
        sizes: [CGSize],
        maxWidth: CGFloat,
        horizontalSpacing: CGFloat,
        verticalSpacing: CGFloat
    ) -> (frames: [CGRect], size: CGSize) {
        var frames: [CGRect] = []
        frames.reserveCapacity(sizes.count)

        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var totalWidth: CGFloat = 0
        var rowIsEmpty = true

        for size in sizes {
            // The row-is-empty guard keeps a child wider than `maxWidth` on its
            // own row instead of wrapping forever.
            if !rowIsEmpty && x + size.width > maxWidth {
                y += rowHeight + verticalSpacing
                x = 0
                rowHeight = 0
                rowIsEmpty = true
            }
            frames.append(CGRect(x: x, y: y, width: size.width, height: size.height))
            x += size.width
            totalWidth = max(totalWidth, x)
            x += horizontalSpacing
            rowHeight = max(rowHeight, size.height)
            rowIsEmpty = false
        }

        let totalHeight = frames.isEmpty ? 0 : y + rowHeight
        return (frames, CGSize(width: totalWidth, height: totalHeight))
    }
}
