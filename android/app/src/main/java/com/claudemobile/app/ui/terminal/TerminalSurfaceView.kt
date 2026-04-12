package com.claudemobile.app.ui.terminal

import android.graphics.Typeface
import android.view.ViewGroup
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.termux.terminal.TerminalSession
import com.termux.terminal.TerminalSessionClient
import com.termux.view.TerminalView
import com.termux.view.TerminalViewClient

/**
 * Compose wrapper around the vendored Termux [TerminalView]. The [session]'s [com.termux.terminal.TerminalEmulator]
 * is owned by the host [TerminalViewModel] so it survives recomposition; this composable is a
 * thin glue layer that creates the View, attaches the session, and forwards layout-driven
 * resize callbacks back to the caller.
 */
@Composable
fun TerminalSurfaceView(
    session: TerminalSession,
    sessionCallback: TerminalSessionCallback,
    onResize: (rows: Int, cols: Int) -> Unit,
    onRowTapped: (text: String) -> Unit = { _ -> },
    modifier: Modifier = Modifier,
) {
    val client = remember { TerminalViewClientImpl() }
    client.onRowTapped = onRowTapped

    DisposableEffect(sessionCallback) {
        onDispose { sessionCallback.onInvalidate = null }
    }

    AndroidView(
        modifier = modifier.fillMaxSize(),
        factory = { ctx ->
            TerminalView(ctx, null).apply {
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT,
                )
                setTextSize(36)
                setTypeface(Typeface.MONOSPACE)
                setTerminalViewClient(client)
                attachSession(session)
                sessionCallback.onInvalidate = { onScreenUpdated() }
                viewTreeObserver.addOnGlobalLayoutListener {
                    val emu = session.emulator
                    if (emu != null) onResize(emu.mRows, emu.mColumns)
                }
            }
        },
        update = { view ->
            if (view.mTermSession !== session) {
                view.attachSession(session)
                sessionCallback.onInvalidate = { view.onScreenUpdated() }
            }
        },
    )
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

    override fun onTextChanged(changedSession: TerminalSession) {
        onScreenUpdated()
        onInvalidate?.invoke()
    }
    override fun onTitleChanged(changedSession: TerminalSession) {}
    override fun onSessionFinished(finishedSession: TerminalSession) {}
    override fun onCopyTextToClipboard(session: TerminalSession, text: String?) {}
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
    var onRowTapped: (String) -> Unit = { _ -> }

    override fun onScale(scale: Float): Float = scale
    override fun onSingleTapUp(e: android.view.MotionEvent?) {}
    override fun onTerminalRowTapped(text: String) { onRowTapped(text) }
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
