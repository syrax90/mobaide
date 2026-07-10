package com.termux.view;

import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;

import com.termux.terminal.TerminalSession;

/**
 * The interface for communication between {@link TerminalView} and its client. It allows for getting
 * various  configuration options from the client and for sending back data to the client like logs,
 * key events, both hardware and IME (which makes it different from that available with
 * {@link View#setOnKeyListener(View.OnKeyListener)}, etc. It must be set for the
 * {@link TerminalView} through {@link TerminalView#setTerminalViewClient(TerminalViewClient)}.
 */
public interface TerminalViewClient {

    /**
     * Callback function on scale events according to {@link ScaleGestureDetector#getScaleFactor()}.
     */
    float onScale(float scale);



    /**
     * On a single tap on the terminal if terminal mouse reporting not enabled.
     */
    void onSingleTapUp(MotionEvent e);

    /**
     * Asked on every confirmed single tap, before input-row resolution. Return true when
     * the host is in composer-hidden direct-typing mode and the tap should raise the IME
     * instead: the tap is then fully consumed, so it can never resolve to an input-row
     * submit ({@link #onTerminalRowTapped}) — tapping the TUI's own prompt row while
     * looking for the keyboard must not fire a CR/execute. Double-tap and long-press
     * are unaffected. Default keeps existing implementors source-/binary-compatible.
     */
    default boolean onTerminalTapToRaiseKeyboard() { return false; }

    /**
     * Side-effect-free query for the same gate as {@link #onTerminalTapToRaiseKeyboard()}
     * (which fires the raise). Consulted on ACTION_UP before the mouse-tracking fast path:
     * when a mouse-tracking TUI is on screen and the composer is hidden, a raise-only tap
     * must not also click the remote UI — the fast path fires immediately, long before the
     * confirmed single tap reaches the raise gate. Default keeps implementors compatible.
     */
    default boolean isTapToRaiseKeyboardActive() { return false; }

    /**
     * Called when the user taps a terminal input line (one starting with "> ").
     * The text parameter contains the message content with the prompt prefix
     * stripped and whitespace trimmed.
     *
     * @param text           the extracted message text
     * @param isCurrentInput true when the cursor is on the tapped input line,
     *                       meaning the PTY's active input buffer likely holds
     *                       this text already
     */
    default void onTerminalRowTapped(String text, boolean isCurrentInput) {}

    /**
     * Called when the user double-taps a terminal input line. Same row→message
     * resolution as {@link #onTerminalRowTapped} (prompt-prefixed start row +
     * wrapped continuation rows joined with newlines), but signals the host to
     * <em>copy</em> the message into the editable input field rather than
     * submit it. Single-tap and double-tap are mutually exclusive at the
     * gesture-recognizer level: a confirmed single-tap fires only after the
     * double-tap timeout expires, so a real double-tap suppresses the submit.
     *
     * @param text           the extracted message text
     * @param isCurrentInput true when the cursor is on the tapped input line
     */
    default void onTerminalRowCopyToInput(String text, boolean isCurrentInput) {}

    /**
     * Called when the user selects arbitrary terminal text via long-press and taps
     * "Send to Input" in the selection {@link android.view.ActionMode} toolbar. Signals
     * the host to copy the free-form selection into the editable input field (cursor at
     * the end, field focused) for editing before resend. Unlike
     * {@link #onTerminalRowCopyToInput}, the source is an arbitrary selection rather than a
     * resolved prompt row; like it, the text is <em>not</em> submitted and is <em>not</em>
     * written to the system clipboard — the separate "Copy" item owns the clipboard.
     *
     * @param text the currently selected terminal text
     */
    default void onCopySelectionToInput(String text) {}

    /**
     * Gate for the "Paste" item in the selection {@link android.view.ActionMode} toolbar,
     * consulted once when the menu is built. Return true when the host can accept a paste
     * right now (e.g. a live running session AND a clipboard that advertises text).
     * Implementations must not read the clipboard <em>contents</em> here — that fires the
     * Android 12+ clipboard-access toast on merely opening the menu; use description-level
     * checks ({@link android.content.ClipboardManager#getPrimaryClipDescription()}) only.
     * Default keeps existing implementors source-/binary-compatible and hides the item.
     */
    default boolean canPasteFromClipboard() { return false; }

    /**
     * Called after a manual drag or fling has settled to a new {@code mTopRow} value
     * (0 = pinned to bottom, negative = scrolled up by N rows). Not called for
     * programmatic {@link TerminalView#setTopRow(int)} writes — those would create
     * a feedback loop with hosts that drive the row from a state flow.
     */
    default void onScrollPositionChanged(int topRow) {}

    boolean shouldBackButtonBeMappedToEscape();

    boolean shouldEnforceCharBasedInput();

    boolean shouldUseCtrlSpaceWorkaround();

    boolean isTerminalViewSelected();



    void copyModeChanged(boolean copyMode);



    boolean onKeyDown(int keyCode, KeyEvent e, TerminalSession session);

    boolean onKeyUp(int keyCode, KeyEvent e);

    boolean onLongPress(MotionEvent event);



    boolean readControlKey();

    boolean readAltKey();

    boolean readShiftKey();

    boolean readFnKey();



    boolean onCodePoint(int codePoint, boolean ctrlDown, TerminalSession session);


    void onEmulatorSet();


    void logError(String tag, String message);

    void logWarn(String tag, String message);

    void logInfo(String tag, String message);

    void logDebug(String tag, String message);

    void logVerbose(String tag, String message);

    void logStackTraceWithMessage(String tag, String message, Exception e);

    void logStackTrace(String tag, Exception e);

}
