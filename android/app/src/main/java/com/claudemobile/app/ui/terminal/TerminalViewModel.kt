package com.claudemobile.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.network.TerminalFrame
import com.claudemobile.app.data.network.TerminalProtocol
import com.claudemobile.app.data.network.WebSocketConnection
import com.claudemobile.app.data.network.WsEvent
import com.claudemobile.app.data.repository.SessionRepository
import com.termux.terminal.TerminalSession
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import okio.ByteString
import org.json.JSONObject
import javax.inject.Inject

/** Live, reconnecting, replaying — what the UI overlay should show on top of the terminal. */
enum class ConnectionState { Live, Reconnecting, ReplayingBuffer }

@HiltViewModel
class TerminalViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val sessionRepository: SessionRepository,
) : ViewModel() {

    private val sessionId: String = savedStateHandle["sessionId"]
        ?: throw IllegalArgumentException("sessionId required")

    private var connection: WebSocketConnection? = null

    /** Termux session driving the renderer. Survives recomposition; created lazily on first connect. */
    val session: TerminalSession = TerminalSession(NoopTerminalSessionClient()).also {
        // 80x24 default until the View measures itself and calls resize().
        it.updateSize(80, 24, 12, 24)
        it.setOnInputListener { data, offset, count ->
            // Bytes the emulator / view want to send to the (remote) PTY — wrap in an 'i' frame.
            val slice = if (offset == 0 && count == data.size) data else data.copyOfRange(offset, offset + count)
            sendBytes(slice)
        }
    }

    private val _sessionState = MutableStateFlow("connecting")
    val sessionState: StateFlow<String> = _sessionState.asStateFlow()

    private val _isConnected = MutableStateFlow(false)
    val isConnected: StateFlow<Boolean> = _isConnected.asStateFlow()

    private val _connectionState = MutableStateFlow(ConnectionState.Reconnecting)
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    /** When true, the next 'o' frame is the start of replay-buffer playback. */
    private var inReplay = false

    init {
        connect()
    }

    private fun connect() {
        connection = sessionRepository.createSessionIoConnection(sessionId)
        connection?.connect()

        viewModelScope.launch {
            connection?.events?.collect { event ->
                when (event) {
                    is WsEvent.Connected -> {
                        _isConnected.value = true
                        _connectionState.value = ConnectionState.Live
                    }
                    is WsEvent.Disconnected -> {
                        _isConnected.value = false
                        _connectionState.value = ConnectionState.Reconnecting
                    }
                    is WsEvent.Error -> {
                        _isConnected.value = false
                        _connectionState.value = ConnectionState.Reconnecting
                    }
                    is WsEvent.Binary -> handleBinary(event.bytes)
                    is WsEvent.Message -> { /* binary protocol only */ }
                }
            }
        }
    }

    private fun handleBinary(frame: ByteString) {
        when (val decoded = TerminalProtocol.decode(frame)) {
            is TerminalFrame.Output -> {
                if (decoded.bytes.isNotEmpty()) {
                    session.emitOutput(decoded.bytes, decoded.bytes.size)
                }
                if (inReplay) {
                    inReplay = false
                    _connectionState.value = ConnectionState.Live
                }
            }
            is TerminalFrame.State -> {
                _sessionState.value = parseStatus(decoded.json)
            }
            TerminalFrame.ReplayMarker -> {
                inReplay = true
                _connectionState.value = ConnectionState.ReplayingBuffer
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

    fun sendInput(text: String) {
        // CR (\r), not LF (\n): Ink/React TUIs in raw mode submit on CR.
        sendBytes((text + "\r").toByteArray(Charsets.UTF_8))
    }

    /** Send raw bytes (Ctrl-C, Esc, arrow keys, ...) to the PTY. */
    fun sendBytes(bytes: ByteArray) {
        connection?.sendBinary(TerminalProtocol.encodeInput(bytes))
    }

    fun resize(rows: Int, cols: Int) {
        session.updateSize(cols, rows, 12, 24)
        connection?.sendBinary(TerminalProtocol.encodeResize(rows, cols))
    }

    override fun onCleared() {
        super.onCleared()
        connection?.disconnect()
    }
}
