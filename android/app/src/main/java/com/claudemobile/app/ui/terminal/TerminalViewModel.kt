package com.claudemobile.app.ui.terminal

import android.util.Base64
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.text.AnnotatedString
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.network.WebSocketConnection
import com.claudemobile.app.data.network.WsEvent
import com.claudemobile.app.data.network.dto.WsInputMessage
import com.claudemobile.app.data.network.dto.WsOutputMessage
import com.claudemobile.app.data.network.dto.WsResizeMessage
import com.claudemobile.app.data.repository.SessionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import javax.inject.Inject

@HiltViewModel
class TerminalViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val sessionRepository: SessionRepository,
) : ViewModel() {

    private val sessionId: String = savedStateHandle["sessionId"]
        ?: throw IllegalArgumentException("sessionId required")

    private val json = Json { ignoreUnknownKeys = true }
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
                    is WsEvent.Message -> handleMessage(event.text)
                }
            }
        }
    }

    private fun handleMessage(text: String) {
        try {
            val msg = json.decodeFromString<WsOutputMessage>(text)
            when (msg.type) {
                "output" -> {
                    val data = msg.data ?: return
                    val decoded = String(Base64.decode(data, Base64.DEFAULT))
                    appendOutput(decoded)
                }
                "state" -> {
                    _sessionState.value = msg.status ?: "unknown"
                }
            }
        } catch (_: Exception) {
            // Ignore malformed messages
        }
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
        val input = text + "\n"
        val encoded = Base64.encodeToString(input.toByteArray(), Base64.NO_WRAP)
        val message = json.encodeToString(WsInputMessage(data = encoded))
        connection?.send(message)
    }

    fun resize(rows: Int, cols: Int) {
        val message = json.encodeToString(WsResizeMessage(rows = rows, cols = cols))
        connection?.send(message)
    }

    override fun onCleared() {
        super.onCleared()
        connection?.disconnect()
    }

    companion object {
        private const val MAX_LINES = 10_000
    }
}
