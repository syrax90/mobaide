package com.claudemobile.app.ui.terminal

import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.text.AnnotatedString
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.network.TerminalFrame
import com.claudemobile.app.data.network.TerminalProtocol
import com.claudemobile.app.data.network.WebSocketConnection
import com.claudemobile.app.data.network.WsEvent
import com.claudemobile.app.data.repository.SessionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import okio.ByteString
import org.json.JSONObject
import javax.inject.Inject

@HiltViewModel
class TerminalViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val sessionRepository: SessionRepository,
) : ViewModel() {

    private val sessionId: String = savedStateHandle["sessionId"]
        ?: throw IllegalArgumentException("sessionId required")

    private var connection: WebSocketConnection? = null

    val lines = mutableStateListOf<AnnotatedString>()

    private val _sessionState = MutableStateFlow("connecting")
    val sessionState: StateFlow<String> = _sessionState.asStateFlow()

    private val _isConnected = MutableStateFlow(false)
    val isConnected: StateFlow<Boolean> = _isConnected.asStateFlow()

    // Partial line buffer for output that doesn't end with newline
    private var partialLine: String = ""

    init {
        connect()
    }

    private fun connect() {
        connection = sessionRepository.createSessionIoConnection(sessionId)
        connection?.connect()

        viewModelScope.launch {
            connection?.events?.collect { event ->
                when (event) {
                    is WsEvent.Connected -> _isConnected.value = true
                    is WsEvent.Disconnected -> _isConnected.value = false
                    is WsEvent.Error -> _isConnected.value = false
                    is WsEvent.Binary -> handleBinary(event.bytes)
                    is WsEvent.Message -> {
                        // Text frames are not used by the binary terminal
                        // protocol — ignore.
                    }
                }
            }
        }
    }

    private fun handleBinary(frame: ByteString) {
        when (val decoded = TerminalProtocol.decode(frame)) {
            is TerminalFrame.Output -> {
                if (decoded.bytes.isNotEmpty()) {
                    appendOutput(String(decoded.bytes, Charsets.UTF_8))
                }
            }
            is TerminalFrame.State -> {
                _sessionState.value = parseStatus(decoded.json)
            }
            TerminalFrame.ReplayMarker -> {
                // History is about to follow. The current line/AnsiParser
                // renderer doesn't need to flush — output is already
                // append-only. The marker is here for the future VT view.
            }
            null -> { /* unknown frame type, ignore */ }
        }
    }

    private fun parseStatus(json: String): String =
        try {
            JSONObject(json).optString("status", "unknown")
        } catch (_: Exception) {
            "unknown"
        }

    private fun appendOutput(text: String) {
        val combined = partialLine + text
        val segments = combined.split('\n')

        if (segments.size == 1) {
            // No newline — accumulate in partial buffer
            partialLine = combined
            // Update the last line in display if exists, or add new
            if (lines.isNotEmpty() && partialLine.isNotEmpty()) {
                lines[lines.size - 1] = AnsiParser.parse(partialLine)
            } else if (partialLine.isNotEmpty()) {
                lines.add(AnsiParser.parse(partialLine))
            }
            return
        }

        // Complete lines
        for (i in 0 until segments.size - 1) {
            val line = if (i == 0) segments[0] else segments[i]
            if (i == 0 && lines.isNotEmpty() && partialLine.isNotEmpty()) {
                // Replace the last partial line with the complete one
                lines[lines.size - 1] = AnsiParser.parse(line)
            } else {
                lines.add(AnsiParser.parse(line))
            }
        }

        // Last segment is the new partial line
        partialLine = segments.last()
        if (partialLine.isNotEmpty()) {
            lines.add(AnsiParser.parse(partialLine))
        }

        // Cap at 10,000 lines
        if (lines.size > MAX_LINES) {
            val excess = lines.size - MAX_LINES
            repeat(excess) { lines.removeAt(0) }
        }
    }

    fun sendInput(text: String) {
        // CR (\r), not LF (\n): Ink/React TUIs in raw mode (claude code)
        // submit on CR. The previous LF was the root cause of "Enter does
        // nothing" on the Android client.
        val bytes = (text + "\r").toByteArray(Charsets.UTF_8)
        sendBytes(bytes)
    }

    /** Send raw bytes (Ctrl-C, Esc, arrow keys, ...) to the PTY. */
    fun sendBytes(bytes: ByteArray) {
        connection?.sendBinary(TerminalProtocol.encodeInput(bytes))
    }

    fun resize(rows: Int, cols: Int) {
        connection?.sendBinary(TerminalProtocol.encodeResize(rows, cols))
    }

    override fun onCleared() {
        super.onCleared()
        connection?.disconnect()
    }

    companion object {
        private const val MAX_LINES = 10_000
    }
}
