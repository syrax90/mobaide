package com.mobaide.app.ui.terminal

import android.graphics.Typeface
import android.view.ViewGroup
import android.view.ViewTreeObserver
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.mobaide.app.data.local.TerminalDisplayConfig
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.ui.common.copyToClipboard
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
    followMode: Boolean = true,
    modifier: Modifier = Modifier,
) {
    val client = remember { TerminalViewClientImpl() }
    client.onRowTapped = onRowTapped
    client.onRowCopyToInput = onRowCopyToInput
    client.onSelectionCopyToInput = onSelectionCopyToInput
    client.onScrollSettled = onScrollSettled

    DisposableEffect(sessionCallback) {
        onDispose {
            sessionCallback.onInvalidate = null
            sessionCallback.onCopyToClipboard = null
        }
    }

    val layoutListener = remember(session) {
        ViewTreeObserver.OnGlobalLayoutListener {
            val emu = session.emulator
            if (emu != null) onResize(emu.mRows, emu.mColumns)
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
                setTextSize(36)
                setTypeface(Typeface.MONOSPACE)
                setTerminalViewClient(client)
                attachSession(session)
                sessionCallback.onInvalidate = { onScreenUpdated() }
                sessionCallback.onCopyToClipboard = { text -> appCtx.copyToClipboard("Terminal output", text, isSensitive = true) }
                applyDisplayConfig(isStatic, staticCols)
                viewTreeObserver.addOnGlobalLayoutListener(layoutListener)
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
                sessionCallback.onInvalidate = { view.onScreenUpdated() }
            }
            view.applyDisplayConfig(isStatic, staticCols)
            if (followMode && view.topRow != 0) {
                view.topRow = 0
                view.invalidate()
            }
        },
        onRelease = { hsv ->
            val view = hsv.getChildAt(0) as TerminalView
            view.viewTreeObserver.removeOnGlobalLayoutListener(layoutListener)
        },
    )
}

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
 * [TerminalSessionClient] that forwards screen-update events to the host ViewModel for prompt
 * detection. All other Termux callbacks (title, bell, clipboard, ...) are no-ops.
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

    override fun onTextChanged(changedSession: TerminalSession) {
        onScreenUpdated()
        onInvalidate?.invoke()
    }
    override fun onTitleChanged(changedSession: TerminalSession) {}
    override fun onSessionFinished(finishedSession: TerminalSession) {}
    override fun onCopyTextToClipboard(session: TerminalSession, text: String?) {
        if (!text.isNullOrEmpty()) onCopyToClipboard?.invoke(text)
    }
    override fun onPasteTextFromClipboard(session: TerminalSession?) {}
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

    override fun onScale(scale: Float): Float = scale
    override fun onSingleTapUp(e: android.view.MotionEvent?) {}
    override fun onTerminalRowTapped(text: String, isCurrentInput: Boolean) { onRowTapped(text, isCurrentInput) }
    override fun onTerminalRowCopyToInput(text: String, isCurrentInput: Boolean) { onRowCopyToInput(text, isCurrentInput) }
    override fun onCopySelectionToInput(text: String) { onSelectionCopyToInput(text) }
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
