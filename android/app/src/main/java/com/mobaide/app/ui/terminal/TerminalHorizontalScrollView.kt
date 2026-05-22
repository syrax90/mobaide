package com.mobaide.app.ui.terminal

import android.content.Context
import android.view.MotionEvent
import android.view.View
import android.widget.HorizontalScrollView

/**
 * Native [HorizontalScrollView] wrapper for the vendored [com.termux.view.TerminalView] used by
 * [TerminalSurfaceView]. Replaces a Compose `Modifier.horizontalScroll`, which used isotropic
 * touch slop and stole the gesture on any horizontal finger drift, killing vertical scroll dead.
 *
 * The native HSV combined with [com.termux.view.TerminalView]'s
 * `requestDisallowInterceptTouchEvent(true)` call on the first vertically-dominant scroll event
 * restores the classic Android intercept-cooperation contract: vertical-dominant gestures stay
 * with the child View, horizontal-dominant pans go to this HSV.
 *
 * Intercept and touch handling are suppressed when the child fits this view's viewport (Full
 * display mode), making this a transparent passthrough so vertical scroll there is unaffected.
 */
internal class TerminalHorizontalScrollView(context: Context) : HorizontalScrollView(context) {

    init {
        isFillViewport = true
        isHorizontalScrollBarEnabled = false
        overScrollMode = View.OVER_SCROLL_NEVER
    }

    override fun onInterceptTouchEvent(ev: MotionEvent): Boolean =
        hasHorizontalOverflow() && super.onInterceptTouchEvent(ev)

    override fun onTouchEvent(ev: MotionEvent): Boolean =
        if (hasHorizontalOverflow()) super.onTouchEvent(ev) else false

    private fun hasHorizontalOverflow(): Boolean {
        val child = getChildAt(0) ?: return false
        return child.width > width
    }
}
