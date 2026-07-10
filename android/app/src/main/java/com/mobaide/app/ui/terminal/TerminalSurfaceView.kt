package com.mobaide.app.ui.terminal

import android.graphics.Typeface
import android.view.ViewGroup
import android.view.ViewTreeObserver
import android.view.inputmethod.InputMethodManager
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.mobaide.app.data.local.TerminalDisplayConfig
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.ui.common.copyToClipboard
import com.mobaide.app.ui.common.hasClipboardText
import com.mobaide.app.ui.common.readClipboardText
import com.termux.terminal.TerminalSession
import com.termux.terminal.TerminalSessionClient
import com.termux.view.TerminalView
import com.termux.view.TerminalViewClient

/**
 * Compose wrapper around the vendored Termux [TerminalView]. The [session]'s emulator is owned
 * by the host [TerminalViewModel] so it survives recomposition; this composable is a thin glue
 * layer that creates the View, attaches the session, and forwards layout-driven resize callbacks
 * back to the caller.
 *
 * The native [TerminalView] is always hosted inside a [TerminalHorizontalScrollView]. In
 * [TerminalDisplayMode.Static], the inner view is laid out wider than the viewport
 * (`staticColumns * mFontWidth`) — the [TerminalView] reports that width to the HSV via its
 * `onMeasure` override — and the HSV provides horizontal panning. In
 * [TerminalDisplayMode.Full] the inner view fills the viewport and the HSV is a transparent
 * passthrough. The HSV's intercept is direction-aware (dominance check `dx > dy`) which,
 * combined with [TerminalView]'s symmetric `requestDisallowInterceptTouchEvent(true)` on
 * vertically-dominant scrolls, keeps vertical scrollback and horizontal panning cleanly
 * independent on any device.
 */
@Composable
fun TerminalSurfaceView(
    session: TerminalSession,
    sessionCallback: TerminalSessionCallback,
    displayConfig: TerminalDisplayConfig,
    onResize: (rows: Int, cols: Int) -> Unit,
    onRowTapped: (text: String, isCurrentInput: Boolean) -> Unit = { _, _ -> },
    onRowCopyToInput: (text: String, isCurrentInput: Boolean) -> Unit = { _, _ -> },
    onSelectionCopyToInput: (text: String) -> Unit = {},
    onScrollSettled: (topRow: Int) -> Unit = {},
    onCursorAnchorChanged: (anchorBottomPx: Int) -> Unit = {},
    followMode: Boolean = true,
    requestKeyboard: Boolean = false,
    onKeyboardHandled: () -> Unit = {},
    pasteFromClipboardEnabled: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val client = remember { TerminalViewClientImpl() }
    val terminalViewRef = remember { mutableStateOf<TerminalView?>(null) }

    // The cursor-anchor reporter feeds imeCursorCounterPan (keyboard cursor-follow).
    // rememberUpdatedState because the reporter is captured by factory-installed hooks
    // (onInvalidate, the layout listener) that outlive the first composition. The
    // fontLineSpacing guard covers the renderer-not-yet-created window (getPointY would
    // NPE) — the global-layout listener re-reports once layout creates the renderer.
    // The 2-row slack keeps bottom-anchored TUIs (Ink parks the caret a row or two above
    // its input-box border) at a zero counter-pan, i.e. exactly the full-pan behavior.
    val currentOnCursorAnchorChanged by rememberUpdatedState(onCursorAnchorChanged)
    val reportCursorAnchor = remember {
        { view: TerminalView ->
            val emulator = view.mTermSession?.emulator
            if (emulator != null && view.rendererFontLineSpacing > 0) {
                currentOnCursorAnchorChanged(
                    view.getPointY(emulator.cursorRow + 1 + CURSOR_ANCHOR_SLACK_ROWS),
                )
            }
        }
    }

    client.onRowTapped = onRowTapped
    client.onRowCopyToInput = onRowCopyToInput
    client.onSelectionCopyToInput = onSelectionCopyToInput
    client.onScrollSettled = { topRow ->
        onScrollSettled(topRow)
        terminalViewRef.value?.let(reportCursorAnchor)
    }
    client.pasteSessionActive = pasteFromClipboardEnabled

    DisposableEffect(sessionCallback) {
        onDispose {
            sessionCallback.onInvalidate = null
            sessionCallback.onCopyToClipboard = null
            sessionCallback.onPasteFromClipboard = null
        }
    }

    // Direct-input mode raises the IME on the TerminalView itself: its InputConnection
    // already routes committed text / IME deletes to the PTY. A plain View never gets
    // the IME auto-shown on touch focus (only an explicit showSoftInput raises it, which
    // upstream Termux does in its onSingleTapUp client and ours deliberately does not),
    // so this programmatic path cannot regress ordinary tap behavior. The post() defers
    // showSoftInput past the same-frame requestFocus race.
    LaunchedEffect(requestKeyboard) {
        if (requestKeyboard) {
            terminalViewRef.value?.let { view ->
                view.requestFocus()
                view.post {
                    view.context.getSystemService(InputMethodManager::class.java)
                        ?.showSoftInput(view, 0)
                }
            }
            onKeyboardHandled()
        }
    }

    val layoutListener = remember(session) {
        ViewTreeObserver.OnGlobalLayoutListener {
            val emu = session.emulator
            if (emu != null) onResize(emu.mRows, emu.mColumns)
            // Layout passes reflow the grid (composer toggle, display-config change,
            // renderer creation) without necessarily emitting output — re-anchor here.
            terminalViewRef.value?.let(reportCursorAnchor)
        }
    }

    val isStatic = displayConfig.mode == TerminalDisplayMode.Static
    val staticCols = displayConfig.staticColumns

    AndroidView(
        modifier = modifier.fillMaxSize(),
        factory = { ctx ->
            val appCtx = ctx.applicationContext
            val terminalView = TerminalView(ctx, null).apply {
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT,
                )
                // Long-press text selection needs View focus: startTextSelectionMode() aborts
                // when requestFocus() fails, and a View is not focusable in touch mode by
                // default. Upstream Termux sets these in activity_termux.xml; this view is built
                // in code, so without them the selection toolbar (Send to Input / Copy) never
                // appears. defaultFocusHighlight off (also matching upstream) avoids a focus ring
                // under D-pad/keyboard navigation; never drawn in touch mode anyway.
                isFocusable = true
                isFocusableInTouchMode = true
                setDefaultFocusHighlightEnabled(false)
                setTextSize(36)
                setTypeface(Typeface.MONOSPACE)
                setTerminalViewClient(client)
                attachSession(session)
                // onScreenUpdated() first: it normalizes mTopRow (auto-scroll to tail)
                // before the anchor is read.
                sessionCallback.onInvalidate = {
                    onScreenUpdated()
                    reportCursorAnchor(this)
                }
                reportCursorAnchor(this)
                sessionCallback.onCopyToClipboard = { text -> appCtx.copyToClipboard("Terminal output", text, isSensitive = true) }
                client.hasClipboardText = { appCtx.hasClipboardText() }
                // Routing through TerminalEmulator.paste() is load-bearing: it strips
                // ESC/C1 controls (escape-sequence injection defense), converts LF→CR,
                // and applies the single canonical ESC[200~/201~ bracketed-paste wrap
                // when the PTY app enabled DECSET 2004. Its mSession.write output then
                // flows through the ViewModel's input listener, which drops bytes
                // unless the session is Live and running. A bracketed paste arrives as
                // three ordered write calls (open marker, body, close marker) — three
                // 'i' frames over the FIFO send channel — which the PTY reassembles;
                // do not merge them here.
                sessionCallback.onPasteFromClipboard = { pasteSession ->
                    val text = appCtx.readClipboardText()
                    if (!text.isNullOrEmpty()) pasteSession.emulator?.paste(text)
                }
                applyDisplayConfig(isStatic, staticCols)
                viewTreeObserver.addOnGlobalLayoutListener(layoutListener)
                terminalViewRef.value = this
            }
            TerminalHorizontalScrollView(ctx).apply {
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT,
                )
                addView(terminalView)
            }
        },
        update = { hsv ->
            val view = hsv.getChildAt(0) as TerminalView
            if (view.mTermSession !== session) {
                view.attachSession(session)
                sessionCallback.onInvalidate = {
                    view.onScreenUpdated()
                    reportCursorAnchor(view)
                }
                // Replayed sessions carry content before the first live frame.
                reportCursorAnchor(view)
            }
            view.applyDisplayConfig(isStatic, staticCols)
            if (followMode && view.topRow != 0) {
                // Bypasses onScreenUpdated, so re-anchor for the new topRow here.
                view.topRow = 0
                view.invalidate()
                reportCursorAnchor(view)
            }
        },
        onRelease = { hsv ->
            val view = hsv.getChildAt(0) as TerminalView
            view.viewTreeObserver.removeOnGlobalLayoutListener(layoutListener)
            if (terminalViewRef.value === view) terminalViewRef.value = null
        },
    )
}

/** Rows of slack under the cursor row included in the reported anchor — see the reporter above. */
private const val CURSOR_ANCHOR_SLACK_ROWS = 2

/** Push the requested display mode into the [TerminalView] without churning when nothing changed. */
private fun TerminalView.applyDisplayConfig(isStatic: Boolean, staticCols: Int) {
    val targetForcedCols = if (isStatic) staticCols else 0
    if (forcedColumns != targetForcedCols) {
        setForcedColumns(targetForcedCols)
    }
    val targetWidth: Int = when {
        !isStatic -> ViewGroup.LayoutParams.MATCH_PARENT
        rendererFontWidth > 0f ->
            kotlin.math.ceil(staticCols * rendererFontWidth).toInt().coerceAtLeast(1)
        // Renderer not ready (zero font width). setForcedColumns will retry on the next
        // layout pass once the renderer is constructed; leave layout width untouched.
        else -> return
    }
    val lp = layoutParams ?: return
    if (lp.width != targetWidth) {
        lp.width = targetWidth
        layoutParams = lp
    }
}

/**
 * [TerminalSessionClient] that forwards screen-update events to the host ViewModel (unread-row
 * tracking) and drives view invalidation, and routes terminal clipboard copy/paste. Cursor-state
 * and the remaining Termux callbacks (title, bell, ...) are no-ops.
 */
class TerminalSessionCallback(
    private val onScreenUpdated: () -> Unit = {},
) : TerminalSessionClient {
    /** Set by the View layer to trigger [TerminalView.onScreenUpdated] → [invalidate]. */
    var onInvalidate: (() -> Unit)? = null

    /**
     * Set by the View layer (which owns a [android.content.Context]) so the terminal can
     * write text to the system clipboard. Reached by two callers: the ActionMode "Copy"
     * item (an explicit user gesture) and the OSC 52 escape sequence emitted by programs in
     * the PTY (e.g. vim/tmux yank) — so enabling this sink also lets remote PTY output set
     * the clipboard without a user gesture. The wiring marks the clip sensitive
     * ([android.content.ClipDescription.EXTRA_IS_SENSITIVE]) because terminal text may
     * contain secrets, which suppresses the clipboard preview on API 33+.
     */
    var onCopyToClipboard: ((String) -> Unit)? = null

    /**
     * Set by the View layer (which owns a [android.content.Context]) so the ActionMode
     * "Paste" item can read the system clipboard and route it through the emulator's
     * sanitized [com.termux.terminal.TerminalEmulator.paste] pipeline. Invoked only from
     * an explicit user gesture — the clipboard read fires the Android 12+ access toast.
     */
    var onPasteFromClipboard: ((TerminalSession) -> Unit)? = null

    override fun onTextChanged(changedSession: TerminalSession) {
        onScreenUpdated()
        onInvalidate?.invoke()
    }
    override fun onTitleChanged(changedSession: TerminalSession) {}
    override fun onSessionFinished(finishedSession: TerminalSession) {}
    override fun onCopyTextToClipboard(session: TerminalSession, text: String?) {
        if (!text.isNullOrEmpty()) onCopyToClipboard?.invoke(text)
    }
    override fun onPasteTextFromClipboard(session: TerminalSession?) {
        if (session != null) onPasteFromClipboard?.invoke(session)
    }
    override fun onBell(session: TerminalSession) {}
    override fun onColorsChanged(session: TerminalSession) {}
    override fun onTerminalCursorStateChange(state: Boolean) {}
    override fun setTerminalShellPid(session: TerminalSession, pid: Int) {}
    override fun getTerminalCursorStyle(): Int? = null
    override fun logError(tag: String?, message: String?) {}
    override fun logWarn(tag: String?, message: String?) {}
    override fun logInfo(tag: String?, message: String?) {}
    override fun logDebug(tag: String?, message: String?) {}
    override fun logVerbose(tag: String?, message: String?) {}
    override fun logStackTraceWithMessage(tag: String?, message: String?, e: Exception?) {}
    override fun logStackTrace(tag: String?, e: Exception?) {}
}

/**
 * Hardware-keyboard / IME glue with modifier key state tracking.
 * Modifier flags are read-and-clear (standard Termux convention): set the flag before
 * dispatching a key event, and [TerminalView] reads and consumes it.
 */
private class TerminalViewClientImpl : TerminalViewClient {
    var ctrlDown = false
    var shiftDown = false
    var altDown = false
    var fnDown = false
    var onRowTapped: (String, Boolean) -> Unit = { _, _ -> }
    var onRowCopyToInput: (String, Boolean) -> Unit = { _, _ -> }
    var onSelectionCopyToInput: (String) -> Unit = {}
    var onScrollSettled: (Int) -> Unit = {}

    /** Session half of the Paste gate (Live + running), pushed by recomposition. */
    var pasteSessionActive = false

    /** Clipboard half of the Paste gate — description-level check, wired by the
     *  factory (needs a Context); must never read clip contents (Android 12+ toast). */
    var hasClipboardText: () -> Boolean = { false }

    override fun onScale(scale: Float): Float = scale
    override fun onSingleTapUp(e: android.view.MotionEvent?) {}
    override fun onTerminalTapToRaiseKeyboard(): Boolean = false
    override fun isTapToRaiseKeyboardActive(): Boolean = false
    override fun onTerminalRowTapped(text: String, isCurrentInput: Boolean) { onRowTapped(text, isCurrentInput) }
    override fun onTerminalRowCopyToInput(text: String, isCurrentInput: Boolean) { onRowCopyToInput(text, isCurrentInput) }
    override fun onCopySelectionToInput(text: String) { onSelectionCopyToInput(text) }
    override fun canPasteFromClipboard(): Boolean = pasteSessionActive && hasClipboardText()
    override fun onScrollPositionChanged(topRow: Int) { onScrollSettled(topRow) }
    override fun shouldBackButtonBeMappedToEscape(): Boolean = false
    override fun shouldEnforceCharBasedInput(): Boolean = true
    override fun shouldUseCtrlSpaceWorkaround(): Boolean = false
    override fun isTerminalViewSelected(): Boolean = true
    override fun copyModeChanged(copyMode: Boolean) {}
    override fun onKeyDown(keyCode: Int, e: android.view.KeyEvent?, session: TerminalSession?): Boolean = false
    override fun onKeyUp(keyCode: Int, e: android.view.KeyEvent?): Boolean = false
    override fun onLongPress(event: android.view.MotionEvent?): Boolean = false
    override fun readControlKey(): Boolean = ctrlDown.also { ctrlDown = false }
    override fun readAltKey(): Boolean = altDown.also { altDown = false }
    override fun readShiftKey(): Boolean = shiftDown.also { shiftDown = false }
    override fun readFnKey(): Boolean = fnDown.also { fnDown = false }
    override fun onCodePoint(codePoint: Int, ctrlDown: Boolean, session: TerminalSession?): Boolean = false
    override fun onEmulatorSet() {}
    override fun logError(tag: String?, message: String?) {}
    override fun logWarn(tag: String?, message: String?) {}
    override fun logInfo(tag: String?, message: String?) {}
    override fun logDebug(tag: String?, message: String?) {}
    override fun logVerbose(tag: String?, message: String?) {}
    override fun logStackTraceWithMessage(tag: String?, message: String?, e: Exception?) {}
    override fun logStackTrace(tag: String?, e: Exception?) {}
}
