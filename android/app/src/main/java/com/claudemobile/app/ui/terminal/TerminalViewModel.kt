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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
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

    private var lastResizeRows = -1
    private var lastResizeCols = -1
    private var resizeJob: Job? = null

    /** Buffered channel for outgoing PTY frames — consumed by a single IO coroutine to
     *  keep sends off the main thread while preserving FIFO ordering. */
    private val sendChannel = Channel<ByteString>(capacity = Channel.BUFFERED)

    private val promptDetector = PromptDetector()

    /** Observable prompt state for the UI to render bottom-sheet overlays. */
    val promptState: StateFlow<PromptState> = promptDetector.promptState

    /** Exposed so the View layer can attach an invalidation hook. */
    val sessionCallback = TerminalSessionCallback(
        onScreenUpdated = {
            // Skip prompt detection during replay to avoid false positives.
            if (_connectionState.value != ConnectionState.ReplayingBuffer) {
                promptDetector.onScreenChanged(viewModelScope, session)
            }
        },
    )

    /** Termux session driving the renderer. Survives recomposition; created once in init. */
    val session: TerminalSession

    init {
        session = TerminalSession(sessionCallback).also {
            // 80x24 default until the View measures itself and calls resize().
            it.updateSize(80, 24, 12, 24)
            it.setOnInputListener { data, offset, count ->
                // Bytes the emulator / view want to send to the (remote) PTY — wrap in an 'i' frame.
                val slice = if (offset == 0 && count == data.size) data else data.copyOfRange(offset, offset + count)
                sendBytes(slice)
            }
        }
        connect()

        // Single consumer drains the send channel on IO, keeping the UI thread free.
        viewModelScope.launch(Dispatchers.IO) {
            for (frame in sendChannel) {
                connection?.sendBinary(frame)
            }
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
        sendChannel.trySend(
            TerminalProtocol.encodeInput((text + "\r").toByteArray(Charsets.UTF_8))
        )
    }

    /** Send raw bytes (Ctrl-C, Esc, arrow keys, ...) to the PTY. */
    fun sendBytes(bytes: ByteArray) {
        sendChannel.trySend(TerminalProtocol.encodeInput(bytes))
    }

    /**
     * Submit a `❯`-prefixed row the user tapped in the terminal as if they pressed Enter.
     *
     * When [isCurrentInput] is true the PTY already holds [text] as its active input —
     * only a bare CR is needed. When false, the tap is on a historical row and the PTY's
     * input buffer holds something else, so an 'x' (execute) frame is used: the server
     * sends Ctrl+C to clear, pauses for the TUI to redraw, then writes [text] + CR.
     *
     * Gated on [ConnectionState.Live] so a tap during reconnect or replay-buffer
     * playback does not queue a send that would land in an unexpected state.
     */
    fun sendTappedRow(text: String, isCurrentInput: Boolean) {
        if (_connectionState.value != ConnectionState.Live) return
        val frame = if (isCurrentInput) {
            TerminalProtocol.encodeInput("\r".toByteArray(Charsets.UTF_8))
        } else {
            TerminalProtocol.encodeExecute((text + "\r").toByteArray(Charsets.UTF_8))
        }
        sendChannel.trySend(frame)
    }

    /** Manually dismiss the currently detected prompt (e.g. user swiped the sheet away). */
    fun dismissPrompt() {
        promptDetector.dismiss()
    }

    fun resize(rows: Int, cols: Int) {
        if (rows == lastResizeRows && cols == lastResizeCols) return
        lastResizeRows = rows
        lastResizeCols = cols
        session.updateSize(cols, rows, 12, 24)
        resizeJob?.cancel()
        resizeJob = viewModelScope.launch {
            delay(100)
            sendChannel.trySend(TerminalProtocol.encodeResize(rows, cols))
        }
    }

    override fun onCleared() {
        super.onCleared()
        sendChannel.close()
        connection?.disconnect()
    }
}
