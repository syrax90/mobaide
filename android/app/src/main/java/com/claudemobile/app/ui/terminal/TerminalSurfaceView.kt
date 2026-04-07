package com.claudemobile.app.ui.terminal

import android.graphics.Typeface
import android.view.ViewGroup
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
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
    onResize: (rows: Int, cols: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val client = remember { NoopTerminalViewClient() }
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
                viewTreeObserver.addOnGlobalLayoutListener {
                    val emu = session.emulator
                    if (emu != null) onResize(emu.mRows, emu.mColumns)
                }
            }
        },
        update = { view ->
            if (view.mTermSession !== session) {
                view.attachSession(session)
            }
        },
    )
}

/**
 * Minimal [TerminalSessionClient] used by the host view-model. The Termux client interface has
 * many hooks (title changes, bell, clipboard, ...) that the Claude Mobile UI does not need —
 * everything is a no-op except [onTextChanged], which the View itself uses to invalidate.
 */
class NoopTerminalSessionClient : TerminalSessionClient {
    override fun onTextChanged(changedSession: TerminalSession) { /* view repaints itself */ }
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

/** Hardware-keyboard / IME glue. We accept all default Termux key handling. */
private class NoopTerminalViewClient : TerminalViewClient {
    override fun onScale(scale: Float): Float = scale
    override fun onSingleTapUp(e: android.view.MotionEvent?) {}
    override fun shouldBackButtonBeMappedToEscape(): Boolean = false
    override fun shouldEnforceCharBasedInput(): Boolean = true
    override fun shouldUseCtrlSpaceWorkaround(): Boolean = false
    override fun isTerminalViewSelected(): Boolean = true
    override fun copyModeChanged(copyMode: Boolean) {}
    override fun onKeyDown(keyCode: Int, e: android.view.KeyEvent?, session: TerminalSession?): Boolean = false
    override fun onKeyUp(keyCode: Int, e: android.view.KeyEvent?): Boolean = false
    override fun onLongPress(event: android.view.MotionEvent?): Boolean = false
    override fun readControlKey(): Boolean = false
    override fun readAltKey(): Boolean = false
    override fun readShiftKey(): Boolean = false
    override fun readFnKey(): Boolean = false
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
