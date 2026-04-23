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
import kotlinx.coroutines.CoroutineStart
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

        // UNDISPATCHED: the body runs inline on this thread until the first
        // suspension point (events.collect), which subscribes synchronously
        // to the SharedFlow. Without this, the WS can open and fire
        // Connected + state + replay + output into a SharedFlow(replay=1)
        // before the collector attaches, leaving the subscriber with only
        // the last frame — the UI then sticks on "Connecting" because the
        // state/replay transitions were lost.
        viewModelScope.launch(start = CoroutineStart.UNDISPATCHED) {
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

        connection?.connect()
    }

    private fun handleBinary(frame: ByteString) {
        // Inbound binary traffic implies the WS is up — if we somehow
        // missed the Connected event, upgrade out of Reconnecting so the
        // overlay doesn't lie.
        _isConnected.value = true
        if (_connectionState.value == ConnectionState.Reconnecting) {
            _connectionState.value = ConnectionState.Live
        }

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

    /** Text from the last row-tap on the *current* input line (isCurrentInput). */
    private var tappedOriginal: String? = null

    /** Called by TerminalScreen when the user taps the current PTY input line. */
    fun onRowTapped(text: String) {
        tappedOriginal = text
    }

    fun sendInput(text: String) {
        val tapped = tappedOriginal
        tappedOriginal = null

        // CR (\r), not LF (\n): Ink/React TUIs in raw mode submit on CR.
        val frame = if (tapped != null && text == tapped) {
            // Unchanged tap on current input — text is already in the PTY,
            // just press Enter to execute it.
            TerminalProtocol.encodeInput("\r".toByteArray(Charsets.UTF_8))
        } else if (tapped != null) {
            // Edited after tapping current input — the PTY still holds the
            // old text. Use 'x' (execute) frame so the server sends Ctrl+C
            // to cancel the stale input, pauses, then writes the new command.
            TerminalProtocol.encodeExecute((text + "\r").toByteArray(Charsets.UTF_8))
        } else {
            // Fresh typed text (no tap involved) — send as regular input.
            TerminalProtocol.encodeInput((text + "\r").toByteArray(Charsets.UTF_8))
        }
        sendChannel.trySend(frame)
    }

    /** Send raw bytes (Ctrl-C, Esc, arrow keys, ...) to the PTY. */
    fun sendBytes(bytes: ByteArray) {
        // Any direct terminal interaction invalidates the tap state — the
        // PTY's input may have changed since the row was tapped.
        tappedOriginal = null
        sendChannel.trySend(TerminalProtocol.encodeInput(bytes))
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
