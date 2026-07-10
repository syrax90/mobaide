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
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.notifications.ForegroundSessionTracker
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
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okio.ByteString
import org.json.JSONObject
import javax.inject.Inject

/** Live, reconnecting, replaying — what the UI overlay should show on top of the terminal. */
enum class ConnectionState { Live, Reconnecting, ReplayingBuffer }

/**
 * User override layered over the auto-derived composer visibility.
 *
 * - [Auto]: composer visible unless direct-input mode auto-hides it.
 * - [ForceVisible]: user revealed it during a direct-mode episode; reset to [Auto]
 *   when the episode ends (a later TUI episode auto-hides again).
 * - [ForceHidden]: user collapsed the composer; sticks across direct-mode episodes
 *   and reconnects until the user reveals it (or the ViewModel is recreated).
 *
 * Whenever the composer is hidden — by auto-hide or [ForceHidden] — the terminal
 * surface IS the input field: auto-raise targets it and typing routes to the PTY.
 */
enum class ComposerOverride { Auto, ForceVisible, ForceHidden }

/** Ctrl+A (0x01) + Ctrl+K (0x0B) — clears the PTY's current input line via
 *  readline line-edit keys (no SIGINT, unlike Ctrl+C), so a composer send never
 *  concatenates onto stale prompt text and a running task is never interrupted.
 *  Holds the two raw control bytes; [TerminalViewModelSendInputTest] asserts them. */
private const val CLEAR_INPUT_LINE = ""

@HiltViewModel
class TerminalViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val sessionRepository: SessionRepository,
    private val templatesRepository: TemplatesRepository,
    private val foregroundSessionTracker: ForegroundSessionTracker,
    private val preferencesStore: PreferencesStore,
) : ViewModel() {

    val displayConfig: StateFlow<TerminalDisplayConfig> = combine(
        preferencesStore.terminalDisplayMode,
        preferencesStore.terminalStaticColumns,
    ) { mode, cols -> TerminalDisplayConfig(mode, cols) }
        .stateIn(
            viewModelScope,
            SharingStarted.Eagerly,
            TerminalDisplayConfig(TerminalDisplayMode.Static, TERMINAL_STATIC_COLUMNS_DEFAULT),
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

    /** Exposed so the View layer can attach an invalidation hook. */
    val sessionCallback = TerminalSessionCallback(
        onScreenUpdated = {
            // Runs on main BEFORE the View's onScreenUpdated() (which calls clearScrollCounter),
            // so emulator.scrollCounter still holds the un-cleared row delta for this update.
            if (!_followMode.value) {
                val rowShift = session.emulator?.scrollCounter ?: 0
                if (rowShift > 0) {
                    _unreadRows.update { (it + rowShift).coerceAtMost(MAX_UNREAD_DISPLAY) }
                }
            }
            sampleTerminalModes()
        },
        onCursorStateChanged = { sampleTerminalModes() },
    )

    /** Termux session driving the renderer. Survives recomposition; created once in init. */
    val session: TerminalSession

    init {
        // 10k transcript rows holds the server's 4 MB ring even after wrap
        // expansion through ANSI/redraws; the Termux emulator clamps to its
        // own [100, 50000] range, so 10000 stays comfortably below the cap.
        session = TerminalSession(TRANSCRIPT_ROWS, sessionCallback).also {
            // 80x24 default until the View measures itself and calls resize().
            it.updateSize(80, 24, 12, 24)
            it.setOnInputListener { data, offset, count ->
                // Bytes the emulator / view want to send to the (remote) PTY — wrap in an
                // 'i' frame. Gated on Live AND a running session (mirrors sendTappedRow
                // plus the visible controls' running gate): an IME raised on the terminal
                // surface must not keep feeding a PTY that stopped or crashed while the
                // WS stayed connected, and emulator auto-replies (DECRQM/DA) to escape
                // sequences re-parsed from the replay ring must not reach the live PTY as
                // stale duplicates.
                if (_connectionState.value != ConnectionState.Live ||
                    _sessionState.value != "running"
                ) {
                    return@setOnInputListener
                }
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

    /** CLI of this session, used to drive the CLI-aware control-key bar. Resolved from
     *  the app-scoped session list (the single source of truth) and, on a cold launch,
     *  from a one-shot REST seed. Defaults to Claude until either resolves, then sticks
     *  to the real kind (immutable per session, so the two sources never disagree). */
    val sessionKind: StateFlow<SessionKind> =
        merge(
            sessionRepository.observeSessionList()
                .mapNotNull { sessions ->
                    sessions.firstOrNull { it.session_id == sessionId }?.kind
                },
            flow {
                // observeSessionList() is WS-pushed with no replay, so a cold launch
                // (notification tap / deep link) can build this VM before the first
                // sessions.changed snapshot — the list starts empty and a Codex session
                // would show the Claude key bar. Seed from a one-shot REST fetch when the
                // live list hasn't already resolved the session. mapNotNull above means
                // the Claude default only ever comes from stateIn's seed value, so there
                // is no Codex→Claude→Codex flicker once a real kind arrives.
                if (sessionRepository.observeSessionList().value
                        .none { it.session_id == sessionId }
                ) {
                    runCatching { sessionRepository.listSessions() }
                        .getOrNull()
                        ?.firstOrNull { it.session_id == sessionId }
                        ?.kind
                        ?.let { emit(it) }
                }
            },
        ).stateIn(viewModelScope, SharingStarted.Eagerly, SessionKind.Claude)

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

    /**
     * Debounced "terminal wants input" latch: DECTCEM cursor visible AND bracketed-paste
     * mode on. Bracketed paste is the strongest client-side proxy for "a program is
     * reading a line of input" — readline (>= 8.1), vim, and the Ink/ratatui AI-CLI input
     * boxes enable it while accepting text and disable it while busy. DECTCEM alone
     * false-positives during TUI redraws, hence the AND plus the [WANTS_INPUT_DEBOUNCE_MS]
     * stability window on both edges.
     */
    private val _wantsInput = MutableStateFlow(false)
    val wantsInput: StateFlow<Boolean> = _wantsInput.asStateFlow()

    /** Alt-screen TUI owns the input (vim, Codex): typed keys route straight to the PTY. */
    private val _directInputMode = MutableStateFlow(false)
    val directInputMode: StateFlow<Boolean> = _directInputMode.asStateFlow()

    /** Composer (text field + action buttons) visibility; auto-hidden in direct-input
     *  mode and user-collapsible anywhere — see [ComposerOverride]. */
    private val _composerVisible = MutableStateFlow(true)
    val composerVisible: StateFlow<Boolean> = _composerVisible.asStateFlow()

    private val _showComposerCoachMark = MutableStateFlow(false)
    val showComposerCoachMark: StateFlow<Boolean> = _showComposerCoachMark.asStateFlow()

    /** One-shot "raise the IME on the terminal surface" signal (direct-input mode). */
    private val _terminalKeyboardRaise = Channel<Unit>(Channel.BUFFERED)
    val terminalKeyboardRaise: Flow<Unit> = _terminalKeyboardRaise.receiveAsFlow()

    private var altScreenActive = false
    private var composerOverride = ComposerOverride.Auto
    private var wantsInputDebounceJob: Job? = null

    /** A wants-input rising edge latched while not Live (replay/reconnect) fires once
     *  when replay playback actually completes (ReplayEndMarker), so opening a session
     *  that already sits at a prompt still auto-raises — without raising mid-replay.
     *  Deliberately survives disconnects: the next genuine replay end releases it
     *  against the then-current latch. */
    private var pendingRaiseAfterOverlay = false

    private val _userMessages = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val userMessages: SharedFlow<String> = _userMessages.asSharedFlow()

    val templates: StateFlow<List<TemplateDto>> = templatesRepository.observeTemplates()

    private val _templatesLoading = MutableStateFlow(false)
    val templatesLoading: StateFlow<Boolean> = _templatesLoading.asStateFlow()

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
                        enterLiveState()
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
            enterLiveState()
        }

        when (val decoded = TerminalProtocol.decode(frame)) {
            is TerminalFrame.Output -> {
                // During replay the chunks are appended without flipping the
                // state — that only happens on REPLAY_END below. The handshake
                // contract is `'b' → 'o'(chunk)…'o'(chunk) → 'e'`, so a stray
                // `'o'` outside replay is live data and is also just appended.
                if (decoded.bytes.isNotEmpty()) {
                    session.emitOutput(decoded.bytes, decoded.bytes.size)
                }
            }
            is TerminalFrame.State -> {
                _sessionState.value = parseStatus(decoded.json)
            }
            TerminalFrame.ReplayMarker -> {
                // The server replays `state + REPLAY marker + chunked ring + REPLAY_END`
                // on EVERY reconnect, not just the first. Without clearing the
                // emulator first, the ring bytes are appended on top of whatever
                // the local emulator already shows from before the drop —
                // visually duplicating the recent history. Emit the standard
                // xterm sequence (cursor-home + erase-display + erase-scrollback)
                // so the replay reconstructs the screen cleanly. A fresh
                // emulator is unaffected since the operations are no-ops on
                // empty buffers.
                session.emitOutput(CLEAR_BEFORE_REPLAY, CLEAR_BEFORE_REPLAY.size)
                inReplay = true
                setFollowMode(true)
                _connectionState.value = ConnectionState.ReplayingBuffer
            }
            TerminalFrame.ReplayEndMarker -> {
                // Server has finished sending the replay chunks; live frames
                // resume after this. Without this explicit terminator the
                // client cannot tell "next chunk of replay" from "live byte"
                // and would either drop the overlay too early (single-frame
                // protocol v2 behaviour) or never drop it at all (chunked v3
                // before this case existed).
                if (inReplay) {
                    inReplay = false
                    enterLiveState(releasePendingRaise = true)
                }
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
        // Wrap multi-line input in xterm bracketed-paste markers so Ink/React TUIs
        // (Claude CLI) treat the whole buffer as a single paste instead of submitting
        // on every embedded LF. Single-line input stays raw — bracketed-paste markers
        // would surface as literal "[200~..." on shells that have not enabled mode 2004.
        // Trade-off: if the user's paste itself contains the raw byte sequence
        // ESC[201~, the receiving TUI will close paste mode early at that point and
        // treat the remainder as keystrokes. Real-world Android clipboards almost never
        // carry raw ESC bytes; the user owns both ends of the session so no privilege
        // boundary is crossed. Strip ESC[201~ from `text` here if a shared-session
        // feature is ever added that lets one user paste into another's session.
        //
        // No trailing CR here: a CR in the same stdin chunk as the text is treated by
        // Ink as pasted content, not Enter. The 'm' (submit) frame makes the server
        // write the payload, pause, then write CR as a separate PTY write.
        val body = if ('\n' in text) {
            "[200~$text[201~"
        } else {
            text
        }
        // Prepend the line clear so this message replaces — never concatenates onto —
        // any stale text already on the PTY's prompt line (e.g. from a double-tap copy).
        val payload = CLEAR_INPUT_LINE + body
        sendChannel.trySend(
            TerminalProtocol.encodeSubmit(payload.toByteArray(Charsets.UTF_8))
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
     * sends Ctrl+C to clear, pauses for the TUI to redraw, writes [text], then submits
     * it with a delayed CR.
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
            TerminalProtocol.encodeExecute(text.toByteArray(Charsets.UTF_8))
        }
        sendChannel.trySend(frame)
    }

    fun updateInputText(value: TextFieldValue) {
        _inputText.value = value
    }

    fun consumeFocusRequest() {
        _requestInputFocus.value = false
    }

    /**
     * Latch point for wants-input detection, called on every screen update and on every
     * DECTCEM transition (both run on main). A sample equal to the current latch cancels
     * any pending commit; a differing sample starts — but never restarts — the debounce
     * timer, whose expiry re-reads live emulator state instead of committing the stale
     * sample. This keeps sub-window flicker (TUI redraws hiding the cursor, shells
     * dropping bracketed paste around a quick command) from ever moving the latch.
     */
    private fun sampleTerminalModes() {
        val emu = session.emulator ?: return
        val alt = emu.isAlternateBufferActive
        if (alt != altScreenActive) {
            altScreenActive = alt
            recomputeDerivedInputState()
        }
        val raw = emu.isCursorEnabled && emu.isBracketedPasteMode
        if (raw == _wantsInput.value) {
            wantsInputDebounceJob?.cancel()
            wantsInputDebounceJob = null
        } else if (wantsInputDebounceJob == null) {
            wantsInputDebounceJob = viewModelScope.launch {
                delay(WANTS_INPUT_DEBOUNCE_MS)
                wantsInputDebounceJob = null
                val now = session.emulator?.let { it.isCursorEnabled && it.isBracketedPasteMode }
                    ?: return@launch
                if (now != _wantsInput.value) commitWantsInput(now)
            }
        }
    }

    private fun commitWantsInput(value: Boolean) {
        _wantsInput.value = value
        if (!value) pendingRaiseAfterOverlay = false
        recomputeDerivedInputState()
        if (value) {
            // Once per rising edge: a user-dismissed keyboard stays dismissed until the
            // state drops and rises again (the next prompt/question).
            if (_connectionState.value == ConnectionState.Live) {
                fireAutoRaise()
            } else {
                pendingRaiseAfterOverlay = true
            }
        }
    }

    /** Raise target follows effective composer visibility, not direct mode: a hidden
     *  composer means the terminal surface is the input field — even at a plain shell
     *  prompt with a user-collapsed composer. [commitWantsInput] recomputes derived
     *  state before calling this, so the visibility read here is current. */
    private fun fireAutoRaise() {
        if (_composerVisible.value) {
            _requestInputFocus.value = true
        } else {
            _terminalKeyboardRaise.trySend(Unit)
        }
    }

    /** Single entry point into [ConnectionState.Live]. A raise deferred by replay is
     *  released only from the genuine ReplayEndMarker ([releasePendingRaise]) — firing
     *  it from WsEvent.Connected or the binary Reconnecting-upgrade would, after a drop
     *  mid-replay, raise under the reconnect overlay using the stale pre-drop latch and
     *  consume the flag before the new replay actually ends. */
    private fun enterLiveState(releasePendingRaise: Boolean = false) {
        val wasLive = _connectionState.value == ConnectionState.Live
        _connectionState.value = ConnectionState.Live
        if (releasePendingRaise && !wasLive && pendingRaiseAfterOverlay && _wantsInput.value) {
            pendingRaiseAfterOverlay = false
            fireAutoRaise()
        }
    }

    private fun recomputeDerivedInputState() {
        val direct = _wantsInput.value && altScreenActive
        val episodeEnded = !direct && _directInputMode.value
        if (episodeEnded && composerOverride == ComposerOverride.ForceVisible) {
            // A manual reveal is scoped to its episode: the next TUI auto-hides again.
            composerOverride = ComposerOverride.Auto
        }
        _directInputMode.value = direct
        val visible = when (composerOverride) {
            ComposerOverride.ForceVisible -> true
            ComposerOverride.ForceHidden -> false
            ComposerOverride.Auto -> !direct
        }
        if (episodeEnded && visible) {
            // Direct-mode episode ended with the composer coming back: the IME may still
            // be focused on the terminal surface from a direct-mode raise — hand focus
            // back to the composer, or a raised keyboard would keep routing keystrokes
            // straight into the PTY while the composer is visible again. Skipped for
            // ForceHidden: there the terminal deliberately stays the input field.
            _requestInputFocus.value = true
        }
        // Coach mark only on AUTO-hide (direct-mode entry) — a manual collapse is the
        // user's own gesture and needs no teaching.
        if (_composerVisible.value && !visible && composerOverride == ComposerOverride.Auto) {
            maybeShowCoachMark()
        }
        _composerVisible.value = visible
    }

    /** Reveal a hidden composer. During a direct-mode episode the choice sticks until
     *  the episode exits ([ComposerOverride.ForceVisible]); otherwise it just clears a
     *  manual collapse back to [ComposerOverride.Auto] — a reveal at an ordinary prompt
     *  (row copy, template pick) must not suppress the auto-hide of a TUI episode that
     *  starts later. */
    fun revealComposer() {
        composerOverride = if (_directInputMode.value) {
            ComposerOverride.ForceVisible
        } else {
            ComposerOverride.Auto
        }
        recomputeDerivedInputState()
        _requestInputFocus.value = true
        _showComposerCoachMark.value = false
    }

    /** Collapse the composer ("hide command field"). Sticks across direct-mode episodes
     *  until [revealComposer]. No focus or raise side effect: the composer unmounting
     *  dismisses the IME naturally, and the user resumes typing by tapping the terminal
     *  (tap-to-raise) or the keyboard button in the control row. */
    fun hideComposer() {
        composerOverride = ComposerOverride.ForceHidden
        recomputeDerivedInputState()
        _showComposerCoachMark.value = false
    }

    private fun maybeShowCoachMark() {
        viewModelScope.launch {
            val seen = runCatching { preferencesStore.composerAutoHideCoachSeen.first() }
                .getOrDefault(true)
            if (!seen) _showComposerCoachMark.value = true
        }
    }

    fun markCoachMarkSeen() {
        _showComposerCoachMark.value = false
        viewModelScope.launch {
            runCatching { preferencesStore.setComposerAutoHideCoachSeen() }
        }
    }

    /** Replace the input bar's contents with [text] and place the cursor at the
     *  end. Used for "tap a row to copy into the input" (`onRowCopyToInput`).
     *  Reveals an auto-hidden composer first — the focus request needs the field
     *  to be composed, and the user's gesture explicitly targets it. */
    fun copyTextToInput(text: String) {
        _inputText.value = TextFieldValue(text = text, selection = TextRange(text.length))
        revealComposer()
    }

    /** Deterministic fetch fired when the templates picker opens. The shared
     *  cache is otherwise only populated best-effort (WS-connect refresh in
     *  UserEventBus, or a visit to Settings → Templates), so without this the
     *  picker can render an empty cache as "no templates". */
    fun refreshTemplates() {
        if (_templatesLoading.value) return
        // Set before launch so the guard holds even off Main.immediate
        // (e.g. StandardTestDispatcher queues the coroutine body).
        _templatesLoading.value = true
        viewModelScope.launch {
            try {
                templatesRepository.refreshTemplates()
            } catch (t: Throwable) {
                _userMessages.tryEmit(
                    "Failed to load templates: ${t.message ?: "unknown error"}",
                )
            } finally {
                _templatesLoading.value = false
            }
        }
    }

    /** Load a template body into the input bar with the inserted text fully
     *  selected, so the user can either send as-is or immediately overtype.
     *  Touches `last_used_at` fire-and-forget so a 5xx never blocks the UX. */
    fun applyTemplate(template: TemplateDto) {
        _inputText.value = TextFieldValue(
            text = template.body,
            selection = TextRange(0, template.body.length),
        )
        revealComposer()
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

    /** Called while this session's terminal is on screen so the foreground
     *  service suppresses a redundant attention notification for the session
     *  the user is already looking at. Alerts for other sessions still fire. */
    fun markScreenVisible() {
        foregroundSessionTracker.setVisible(sessionId)
    }

    fun markScreenHidden() {
        foregroundSessionTracker.clearVisible(sessionId)
    }

    /**
     * Wake the session WebSocket up when the terminal screen becomes
     * visible again — typically after the device screen turned back on or
     * the app returned from background. While the screen was off the WS may
     * have dropped (Doze restricts network) and the exponential backoff
     * inside [WebSocketConnection.scheduleReconnect] can be sitting at the
     * 30 s ceiling, so without this nudge the user can stare at the
     * "Reconnecting…" overlay for up to half a minute. No-op when the
     * connection is already [ConnectionState.Live]: forcing a reconnect on
     * a healthy socket would burn a server replay round-trip for nothing
     * and flash the overlay unnecessarily.
     */
    fun onScreenVisible() {
        if (_connectionState.value == ConnectionState.Live) return
        connection?.forceReconnect()
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

        /**
         * Stability window for the wants-input latch. TUI redraws hide the cursor for a
         * frame or two (well under 100 ms); interactive prompts hold DECTCEM+2004 stable
         * for far longer. 400 ms sits between the two with a wide margin on both sides.
         */
        const val WANTS_INPUT_DEBOUNCE_MS = 400L

        /**
         * Local emulator transcript size in rows. Sized to comfortably hold
         * the server's full ring-buffer replay (~4 MB raw PTY bytes, which
         * unwrap to a few thousand effective rows even with dense ANSI/redraw
         * overhead). Stays well below Termux's TERMINAL_TRANSCRIPT_ROWS_MAX
         * of 50_000 — lifting this further is cheap (rows are allocated
         * lazily) if very long sessions become common.
         */
        const val TRANSCRIPT_ROWS = 10_000

        /**
         * `ESC [ H` (cursor home) + `ESC [ 2 J` (erase entire display) +
         * `ESC [ 3 J` (erase saved/scrollback lines — xterm extension,
         * handled by Termux's [com.termux.terminal.TerminalEmulator] CSI 'J'
         * branch which calls `mMainBuffer.clearTranscript()`). Emitted to
         * the emulator on every replay-marker frame to drop any stale local
         * content before the server's ring buffer is replayed.
         */
        val CLEAR_BEFORE_REPLAY: ByteArray = byteArrayOf(
            0x1b, '['.code.toByte(), 'H'.code.toByte(),
            0x1b, '['.code.toByte(), '2'.code.toByte(), 'J'.code.toByte(),
            0x1b, '['.code.toByte(), '3'.code.toByte(), 'J'.code.toByte(),
        )
    }
}
