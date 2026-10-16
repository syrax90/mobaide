package com.termux.view.textselection;

import android.view.MotionEvent;
import android.view.ViewTreeObserver;

import com.termux.view.TerminalView;

/**
 * A CursorController instance can be used to control cursors in the text.
 * It is not used outside of {@link TerminalView}.
 */
public interface CursorController extends ViewTreeObserver.OnTouchModeChangeListener {
    /**
     * Show the cursors on screen. Will be drawn by {@link #render()} by a call during onDraw.
     * See also {@link #hide()}.
     */
    void show(MotionEvent event);

    /**
     * Hide the cursors from screen.
     * See also {@link #show(MotionEvent event)}.
     */
    boolean hide();

    /**
     * Render the cursors.
     */
    void render();

    /**
     * Update the cursor positions.
     */
    void updatePosition(TextSelectionHandleView handle, int x, int y);

    /**
     * @return whether the last {@link #updatePosition} scrolled the view to follow a handle held
     * at an edge, so the dragged handle should repeat it while the finger stays put.
     */
    default boolean didAutoscroll() {
        return false;
    }

    /**
     * This method is called by {@link #onTouchEvent(MotionEvent)} and gives the cursors
     * a chance to become active and/or visible.
     *
     * @param event The touch event
     */
    boolean onTouchEvent(MotionEvent event);

    /**
     * Called when the view is detached from window. Perform house keeping task, such as
     * stopping Runnable thread that would otherwise keep a reference on the context, thus
     * preventing the activity to be recycled.
     */
    void onDetached();

    /**
     * @return true if the cursors are currently active.
     */
    boolean isActive();

}
