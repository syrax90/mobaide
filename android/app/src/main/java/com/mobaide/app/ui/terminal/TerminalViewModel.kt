package com.mobaide.app.ui.terminal

import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayConfig
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.TerminalFrame
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.termux.terminal.TerminalSession
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
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
    private val templatesRepository: TemplatesRepository,
    preferencesStore: PreferencesStore,
) : ViewModel() {

    val displayConfig: StateFlow<TerminalDisplayConfig> = combine(
        preferencesStore.terminalDisplayMode,
        preferencesStore.terminalStaticColumns,
    ) { mode, cols -> TerminalDisplayConfig(mode, cols) }
        .stateIn(
            viewModelScope,
            SharingStarted.Eagerly,
            TerminalDisplayConfig(TerminalDisplayMode.Full, TERMINAL_STATIC_COLUMNS_DEFAULT),
        )

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
            // Runs on main BEFORE the View's onScreenUpdated() (which calls clearScrollCounter),
            // so emulator.scrollCounter still holds the un-cleared row delta for this update.
            if (!_followMode.value) {
                val rowShift = session.emulator?.scrollCounter ?: 0
                if (rowShift > 0) {
                    _unreadRows.update { (it + rowShift).coerceAtMost(MAX_UNREAD_DISPLAY) }
                }
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

    private val _followMode = MutableStateFlow(true)
    val followMode: StateFlow<Boolean> = _followMode.asStateFlow()

    private val _unreadRows = MutableStateFlow(0)
    val unreadRows: StateFlow<Int> = _unreadRows.asStateFlow()

    private val _inputText = MutableStateFlow(TextFieldValue(""))
    val inputText: StateFlow<TextFieldValue> = _inputText.asStateFlow()

    private val _requestInputFocus = MutableStateFlow(false)
    val requestInputFocus: StateFlow<Boolean> = _requestInputFocus.asStateFlow()

    private val _userMessages = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val userMessages: SharedFlow<String> = _userMessages.asSharedFlow()

    val templates: StateFlow<List<TemplateDto>> = templatesRepository.observeTemplates()

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
                setFollowMode(true)
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
        setFollowMode(true)
        // CR (\r), not LF (\n): Ink/React TUIs in raw mode submit on CR.
        sendChannel.trySend(
            TerminalProtocol.encodeInput((text + "\r").toByteArray(Charsets.UTF_8))
        )
        _inputText.value = TextFieldValue("")
    }

    /** Send raw bytes (Ctrl-C, Esc, arrow keys, ...) to the PTY. */
    fun sendBytes(bytes: ByteArray) {
        setFollowMode(true)
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
        setFollowMode(true)
        val frame = if (isCurrentInput) {
            TerminalProtocol.encodeInput("\r".toByteArray(Charsets.UTF_8))
        } else {
            TerminalProtocol.encodeExecute((text + "\r").toByteArray(Charsets.UTF_8))
        }
        sendChannel.trySend(frame)
    }

    fun updateInputText(value: TextFieldValue) {
        _inputText.value = value
    }

    fun consumeFocusRequest() {
        _requestInputFocus.value = false
    }

    /** Replace the input bar's contents with [text] and place the cursor at the
     *  end. Used for "tap a row to copy into the input" (`onRowCopyToInput`). */
    fun copyTextToInput(text: String) {
        _inputText.value = TextFieldValue(text = text, selection = TextRange(text.length))
        _requestInputFocus.value = true
    }

    /** Load a template body into the input bar with the inserted text fully
     *  selected, so the user can either send as-is or immediately overtype.
     *  Touches `last_used_at` fire-and-forget so a 5xx never blocks the UX. */
    fun applyTemplate(template: TemplateDto) {
        _inputText.value = TextFieldValue(
            text = template.body,
            selection = TextRange(0, template.body.length),
        )
        _requestInputFocus.value = true
        viewModelScope.launch {
            try {
                templatesRepository.touchTemplate(template.template_id)
            } catch (_: Throwable) {
                // Touch is purely a UX nicety (recents ordering); silently
                // ignore failures.
            }
        }
    }

    fun saveCurrentInputAsTemplate(name: String) {
        val body = _inputText.value.text
        val trimmedName = name.trim()
        if (body.isEmpty() || trimmedName.isEmpty()) return
        viewModelScope.launch {
            try {
                templatesRepository.createTemplate(trimmedName, body)
                _userMessages.tryEmit("Saved template \"$trimmedName\"")
            } catch (t: Throwable) {
                _userMessages.tryEmit(
                    "Failed to save template: ${t.message ?: "unknown error"}",
                )
            }
        }
    }

    fun deleteTemplate(templateId: String) {
        viewModelScope.launch {
            try {
                templatesRepository.deleteTemplate(templateId)
            } catch (_: Throwable) {
                _userMessages.tryEmit("Failed to delete template")
            }
        }
    }

    fun toggleTemplateFavorite(template: TemplateDto) {
        viewModelScope.launch {
            try {
                templatesRepository.updateTemplate(
                    template.template_id,
                    isFavorite = !template.is_favorite,
                )
            } catch (_: Throwable) {
                _userMessages.tryEmit("Failed to update template")
            }
        }
    }

    fun setFollowMode(enabled: Boolean) {
        if (_followMode.value == enabled) return
        _followMode.value = enabled
        session.emulator?.let { emu ->
            if (emu.isAutoScrollDisabled != !enabled) emu.toggleAutoScrollDisabled()
        }
        if (enabled) _unreadRows.value = 0
    }

    fun onUserScrollSettled(topRow: Int) {
        if (topRow == 0) setFollowMode(true)
        else if (topRow < 0) setFollowMode(false)
    }

    fun jumpToBottom() {
        setFollowMode(true)
    }

    /** Manually dismiss the currently detected prompt (e.g. user swiped the sheet away). */
    fun dismissPrompt() {
        promptDetector.dismiss()
    }

    fun resize(rows: Int, cols: Int) {
        if (rows == lastResizeRows && cols == lastResizeCols) return
        lastResizeRows = rows
        lastResizeCols = cols
        // TerminalView.updateSize() already calls session.updateSize(...) with real
        // renderer metrics synchronously before this callback fires; calling it again here
        // with stub (12, 24) metrics would clobber the correct values.
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

    private companion object {
        const val MAX_UNREAD_DISPLAY = 9999
    }
}
