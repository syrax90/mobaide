package com.mobaide.app.ui.terminal

import android.content.Context
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.widget.HorizontalScrollView
import com.termux.view.TerminalView
import kotlin.math.abs

/**
 * Native [HorizontalScrollView] wrapper for the vendored [com.termux.view.TerminalView] used by
 * [TerminalSurfaceView]. Replaces a Compose `Modifier.horizontalScroll`, whose isotropic touch
 * slop stole the gesture on any horizontal finger drift and killed vertical scroll dead.
 *
 * Two cooperating mechanisms keep vertical and horizontal scroll independent:
 *
 *  1. **Direction-aware intercept (this class):** on a finger gesture, only intercept once
 *     horizontal motion past [ViewConfiguration.getScaledTouchSlop] is strictly dominant
 *     (`dx > dy`). Vertical-dominant gestures are left to the child even if their horizontal
 *     drift crosses slop. The decision latches per gesture — once we have claimed for
 *     horizontal pan we keep claiming until ACTION_UP/CANCEL so the user can pan smoothly
 *     without re-arming. Mirrors the dominance contract that [com.termux.view.TerminalView]
 *     uses on its side (`|distanceY| >= |distanceX|` → disallow intercept).
 *
 *  2. **Child-side disallow ([com.termux.view.TerminalView]'s `requestDisallowInterceptTouchEvent`):**
 *     belt-and-suspenders. Mostly redundant with (1) but cheap, and forward-compatible with
 *     any future ancestor ViewGroup added above this HSV that also tries to intercept.
 *
 * Both `onInterceptTouchEvent` and `onTouchEvent` short-circuit to a transparent passthrough
 * when the child fits the viewport (Full display mode), so events flow straight to the child
 * untouched.
 *
 * [revealCursor] pans to the cursor column while the user types (see [TerminalCursorReveal]).
 */
internal class TerminalHorizontalScrollView(context: Context) : HorizontalScrollView(context) {

    private val touchSlop: Int = ViewConfiguration.get(context).scaledTouchSlop
    private var downX: Float = 0f
    private var downY: Float = 0f
    private var horizontalDragDecided: Boolean = false
    private var touchActive: Boolean = false

    /** Fired when the user claims a horizontal pan: their intent outranks a pending cursor reveal. */
    var onHorizontalDragStart: (() -> Unit)? = null

    init {
        isFillViewport = true
        isHorizontalScrollBarEnabled = false
        overScrollMode = View.OVER_SCROLL_NEVER
    }

    override fun onInterceptTouchEvent(ev: MotionEvent): Boolean {
        if (!hasHorizontalOverflow()) return false
        when (ev.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = ev.x
                downY = ev.y
                horizontalDragDecided = false
            }
            MotionEvent.ACTION_MOVE -> {
                if (!horizontalDragDecided) {
                    val dx = abs(ev.x - downX)
                    val dy = abs(ev.y - downY)
                    if (dx > touchSlop && dx > dy) {
                        horizontalDragDecided = true
                        onHorizontalDragStart?.invoke()
                    }
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                horizontalDragDecided = false
            }
        }
        super.onInterceptTouchEvent(ev)
        return horizontalDragDecided
    }

    override fun onTouchEvent(ev: MotionEvent): Boolean =
        if (hasHorizontalOverflow()) super.onTouchEvent(ev) else false

    // Tracked here, not in the intercept/touch handlers: a vertical scrollback drag makes the
    // child disallow intercept, after which neither sees the lift.
    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        when (ev.actionMasked) {
            MotionEvent.ACTION_DOWN -> touchActive = true
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> touchActive = false
        }
        return super.dispatchTouchEvent(ev)
    }

    /**
     * Pans the minimum needed (plus lookahead) to bring the cursor cell on screen, when [reveal]
     * is armed and the cursor moved. Instant [scrollTo], no animation: only the offset changes,
     * never a size, so the PTY never sees a resize.
     */
    fun revealCursor(view: TerminalView, reveal: TerminalCursorReveal) {
        if (!reveal.isArmed || touchActive || view.isSelectingText) return
        if (view.parent !== this || !hasHorizontalOverflow() || view.rendererFontWidth <= 0f) return
        val emulator = view.mEmulator ?: return
        // A paint can land mid-frame (coalesced ESU..BSU, safety flush) with the cursor already
        // partway into the next frame; iOS never paints there at all.
        if (emulator.isSynchronizedOutputActive) return
        val col = emulator.cursorCol
        if (!reveal.shouldReveal(col, emulator.cursorRow)) return
        val target = TerminalCursorReveal.targetScrollX(
            cursorX = view.getPointX(col).toFloat(),
            cellWidth = view.rendererFontWidth,
            viewportWidth = width,
            contentWidth = view.width,
            currentScrollX = scrollX,
        ) ?: return
        scrollTo(target, 0)
    }

    private fun hasHorizontalOverflow(): Boolean {
        val child = getChildAt(0) ?: return false
        return child.width > width
    }
}
