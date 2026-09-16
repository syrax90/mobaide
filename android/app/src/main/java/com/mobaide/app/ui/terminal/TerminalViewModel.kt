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
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.TerminalFrame
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.toGitCapabilities
import com.mobaide.app.data.network.dto.wireName
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.ShortcutsRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.ui.sessions.sessionHeaderLabel
import com.termux.terminal.TerminalSession
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.shareIn
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okio.ByteString
import org.json.JSONObject
import javax.inject.Inject

/**
 * Live, reconnecting, replaying, suspended — what the UI overlay should show on
 * top of the terminal.
 *
 * [Suspended] is deliberate, not a failure: nothing on screen was reading this
 * session, so its socket was closed to stop paying for output nobody sees. It
 * reopens by itself the moment the screen comes back, which is why it carries
 * no retry affordance. Kept distinct from [Reconnecting] so the input gates and
 * the overlay cannot mistake it for a dropped connection.
 */
enum class ConnectionState { Live, Reconnecting, ReplayingBuffer, Suspended }

/** Ctrl+A (0x01) + Ctrl+K (0x0B) — clears the PTY's current input line via
 *  readline line-edit keys (no SIGINT, unlike Ctrl+C), so a composer send never
 *  concatenates onto stale prompt text and a running task is never interrupted.
 *  Holds the two raw control bytes; [TerminalViewModelSendInputTest] asserts them. */
private const val CLEAR_INPUT_LINE = ""

@HiltViewModel
class TerminalViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val sessionRepository: SessionRepository,
    private val customAgentTypesRepository: CustomAgentTypesRepository,
    private val templatesRepository: TemplatesRepository,
    private val shortcutsRepository: ShortcutsRepository,
    private val foregroundSessionTracker: ForegroundSessionTracker,
    private val appForegroundTracker: AppForegroundTracker,
    private val preferencesStore: PreferencesStore,
) : ViewModel() {

    /**
     * The suspend policy, overridable in tests so a case can exercise this
     * wiring without pushing [TerminalSuspend.RECOUP_BYTES] through the
     * emulator. `TerminalSuspendPolicyTest` owns the thresholds themselves.
     */
    internal var suspendPolicy: (Long, Long) -> Boolean = TerminalSuspend::shouldSuspend

    /** This session's terminal is composed on screen. */
    private var screenVisible = false
    private var appForeground = true

    /** A session-scoped child screen (git Changes / Diff) is composed on top. */
    private var heldByChild = false

    /** When the session stopped being read, or null while it is being read. */
    private var unwatchedSinceMs: Long? = null

    /** Live output charged since then. Replay spans are not charged. */
    private var unwatchedBytes = 0L
    private var streamSuspended = false

    /** Consumed by the ON_START nudge so a resume is not reconnected twice. */
    private var resumedFromSuspend = false

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
        },
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
                val slice = if (offset == 0 && count == data.size) data else data.copyOfRange(offset, offset + count)
                sendTerminalBytes(slice)
            }
        }
        connect()

        viewModelScope.launch {
            appForegroundTracker.isForeground.collect { foreground ->
                appForeground = foreground
                syncWatched()
            }
        }

        viewModelScope.launch {
            foregroundSessionTracker.watchHolds
                .map { holds -> (holds[sessionId] ?: 0) > 0 }
                .distinctUntilChanged()
                .collect { held ->
                    heldByChild = held
                    syncWatched()
                }
        }

        // Single consumer drains the send channel on IO, keeping the UI thread free.
        viewModelScope.launch(Dispatchers.IO) {
            for (frame in sendChannel) {
                connection?.sendBinary(frame)
            }
        }
    }

    private val _sessionState = MutableStateFlow("connecting")
    val sessionState: StateFlow<String> = _sessionState.asStateFlow()

    /** The session's kind once actually resolved, null until then. Consumed by
     *  [savedShortcutsForKind], which must not render another kind's saved
     *  buttons while the kind is unresolved (they would be tappable in the
     *  wrong session kind).
     *
     *  Two writers, both authoritative: the [sessionKind] merge below, and
     *  [saveShortcut], which publishes what its own resolution found so a save
     *  cannot land while this gate is still shut. They can therefore disagree
     *  on a cold launch whose REST seed failed — this may hold the real kind
     *  while [sessionKind] is still null until a WS push corrects it. Do not
     *  assume `resolvedKind == sessionKind`. */
    private val resolvedKind = MutableStateFlow<SessionKind?>(null)

    /** This session's row, from the app-scoped session list (the single source of
     *  truth) plus a one-shot REST seed for a cold launch.
     *
     *  observeSessionList() starts empty, so a cold launch (notification tap /
     *  deep link) can build this VM before the first sessions.changed snapshot —
     *  listSessions() is a pass-through that never writes the cache, so without
     *  the seed both consumers below would stay unresolved until some later
     *  session event, or for the whole visit if the event bus never connects.
     *  Delegates to resolvedSession() — the single implementation of
     *  live-list-then-REST resolution, shared with saveShortcut.
     *
     *  Shared (not two independent merges) so the seed costs one fetch, and
     *  mapNotNull'd so a row that vanishes from the list leaves the last value
     *  standing rather than reverting its consumers. */
    private val sessionRow: Flow<SessionResponse> =
        merge(
            sessionRepository.observeSessionList()
                .mapNotNull { sessions -> sessions.firstOrNull { it.session_id == sessionId } },
            flow { resolvedSession()?.let { emit(it) } },
        )
            .shareIn(viewModelScope, SharingStarted.Eagerly, replay = 1)

    /** CLI of this session, used to drive the CLI-aware control-key bar. Null until
     *  [sessionRow] resolves — consumers render the kind-neutral default (plain key
     *  bar, no saved shortcuts, no MRU writes) rather than guessing Claude, which
     *  would flash the wrong bar and mislabel MRU buckets for custom/Codex sessions
     *  — then sticks to the real kind (immutable per session, so the live list and
     *  the REST seed never disagree). [sessionRow] only ever emits a real row, so
     *  null comes from stateIn's seed value alone and there is no Codex→null→Codex
     *  flicker once a kind arrives. */
    val sessionKind: StateFlow<SessionKind?> =
        sessionRow
            .map { it.kind }
            .onEach { resolvedKind.value = it }
            .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** What the top bar names this session: its live OSC title while there is
     *  one, else the kind label. Empty only until [sessionRow] resolves. */
    val headerLabel: StateFlow<String> =
        combine(sessionRow, customAgentTypesRepository.observeTypes()) { session, types ->
            sessionHeaderLabel(session, types.associate { "custom_${it.id}" to it.name })
        }
            .stateIn(viewModelScope, SharingStarted.Eagerly, "")

    /** The directory the session was spawned in; gates the git overflow entry.
     *  Null until [sessionRow] resolves, or for a session an older agent
     *  reported without one. */
    val sessionCwd: StateFlow<String?> =
        sessionRow
            .map { it.cwd }
            .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** The agent's `capabilities.git`: one `GET /api/version` probe per
     *  subscription, every key read as an explicit `true`; an older agent or
     *  an unreadable probe hides every gated entry. */
    val gitCapabilities: StateFlow<GitCapabilities> =
        flow {
            emit(
                runCatching { sessionRepository.getAgentVersion().toGitCapabilities() }
                    .getOrDefault(GitCapabilities.NONE),
            )
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), GitCapabilities.NONE)

    /** Whether the `⋯` menu offers Commit… (`capabilities.git.commit`). */
    val gitCommitAvailable: StateFlow<Boolean> =
        gitCapabilities.map { it.commit }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), false)

    /** Whether the `⋯` menu offers Fetch / Pull / Push… (any of the three job keys). */
    val gitSyncAvailable: StateFlow<Boolean> =
        gitCapabilities.map { it.hasSync }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), false)

    /** The project the session belongs to; Branches… links to its Git screen. */
    val sessionProjectName: StateFlow<String?> =
        sessionRow
            .map { it.project_name }
            .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** Whether the `⋯` menu offers Branches… (`capabilities.git.branches`). */
    val gitBranchesAvailable: StateFlow<Boolean> =
        gitCapabilities.map { it.branches }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), false)

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

    /** Composer (text field + action buttons) visibility. Purely user-controlled:
     *  the hide button collapses it, the reveal chip brings it back, and the choice
     *  sticks across TUI episodes and reconnects (until the ViewModel is recreated).
     *  While hidden, the terminal surface IS the input field: the Show-keyboard button
     *  raises the IME on it and typing routes straight to the PTY. */
    private val _composerVisible = MutableStateFlow(true)
    val composerVisible: StateFlow<Boolean> = _composerVisible.asStateFlow()

    private val _userMessages = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val userMessages: SharedFlow<String> = _userMessages.asSharedFlow()

    val templates: StateFlow<List<TemplateDto>> = templatesRepository.observeTemplates()

    private val _templatesLoading = MutableStateFlow(false)
    val templatesLoading: StateFlow<Boolean> = _templatesLoading.asStateFlow()

    /** MRU shortcut chords for the current session kind, newest first. Loaded
     *  lazily via [refreshShortcutRecents]/[sendChord] — never in init, so the
     *  strict-mock PreferencesStore stubs in existing ViewModel tests stay minimal. */
    private val _shortcutRecents = MutableStateFlow<List<KeyChord>>(emptyList())
    val shortcutRecents: StateFlow<List<KeyChord>> = _shortcutRecents.asStateFlow()

    /** Server-synced saved shortcuts for this session's kind, in save order.
     *  Gated on [resolvedKind]: while the kind is unresolved the list stays
     *  empty rather than rendering the eager Claude seed's bucket (whose
     *  buttons would be tappable in a not-yet-resolved Codex/Antigravity
     *  session). `by lazy` keeps the repository untouched until the UI first
     *  collects — the same strict-mock laziness contract [shortcutRecents]
     *  documents, extended to [ShortcutsRepository] (an eager field
     *  initializer would call `observeShortcuts()` during construction in
     *  every VM test). */
    val savedShortcutsForKind: StateFlow<List<SavedShortcutUi>> by lazy {
        combine(shortcutsRepository.observeShortcuts(), resolvedKind) { all, kind ->
            if (kind == null) return@combine emptyList()
            all.filter { it.kind == kind.wireName }
                .sortedWith(
                    compareBy({ it.position }, { it.created_at }, { it.shortcut_id })
                )
                .mapNotNull { dto ->
                    KeyChord.parse(dto.chord_token)?.let { chord ->
                        SavedShortcutUi(
                            id = dto.shortcut_id,
                            chord = chord,
                            name = dto.name?.takeIf { it.isNotBlank() },
                            risky = KeyChordEncoder.encode(chord)?.usesCsiU == true,
                        )
                    }
                }
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())
    }

    private val _savedShortcutsLoading = MutableStateFlow(false)

    /** When true, the next 'o' frame is the start of replay-buffer playback. */
    private var inReplay = false

    private val _sessionEnded = Channel<Unit>(Channel.BUFFERED)

    /** Fires once when the agent proves this session no longer exists and it was
     *  never seen alive on this screen — the caller leaves rather than render a
     *  terminal for a session that is gone (stale auto-resume id, restored back
     *  stack, tapped notification for a session that has since ended). */
    val sessionEnded: Flow<Unit> = _sessionEnded.receiveAsFlow()

    /** Once the session is known to exist, it is never treated as missing again:
     *  a session ending or being removed while the user is reading it must leave
     *  the output on screen, not eject them. */
    private var everSeenAlive = false
    private var sessionEndedEmitted = false

    // Declared after the state properties above so their backing fields are
    // initialized before this observer can touch them.
    init {
        viewModelScope.launch {
            combine(
                sessionRepository.observeSessionList(),
                sessionRepository.observeSessionsSynced(),
            ) { sessions, synced -> sessions to synced }
                .collect { (sessions, synced) ->
                    if (sessions.any { it.session_id == sessionId }) {
                        everSeenAlive = true
                    } else if (synced && !everSeenAlive && !sessionEndedEmitted) {
                        sessionEndedEmitted = true
                        sessionRepository.notifySessionEnded(sessionId)
                        _sessionEnded.trySend(Unit)
                    }
                }
        }
    }

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
                        if (!streamSuspended) {
                            _connectionState.value = ConnectionState.Reconnecting
                        }
                    }
                    is WsEvent.Error -> {
                        _isConnected.value = false
                        // A send attempted over the socket a suspend just
                        // closed — a debounced resize is the reachable one —
                        // emits Error. Left ungated it would replace "Paused"
                        // with a reconnect the governor is not performing.
                        if (!streamSuspended) {
                            _connectionState.value = ConnectionState.Reconnecting
                        }
                    }
                    is WsEvent.Binary -> {
                        handleBinary(event.bytes)
                        // Charged after the frame is handled, never before: a
                        // suspend resets the replay machine, and this same
                        // frame would then be read against the wrong state.
                        chargeInbound(event.bytes.size.toLong())
                    }
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
        //
        // Not while suspended, though. The events flow buffers, so the frames
        // already queued behind the one that tripped the policy are delivered
        // *after* `suspendStream` closed the socket, and this upgrade would
        // hand them back a connected flag. `InputBar` gates only on
        // `isConnected && sessionState`, so the composer would reopen over a
        // dead socket and `sendInput` would clear the user's text into a frame
        // that never leaves.
        if (streamSuspended) return
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
                    session.emitOutput(decoded.bytes, decoded.bytes.size, inReplay)
                }
            }
            is TerminalFrame.State -> {
                val status = parseStatus(decoded.json)
                // The agent answering with a live PTY is proof the session
                // exists, independent of when its list snapshot lands.
                if (status == "running" || status == "starting") {
                    everSeenAlive = true
                }
                _sessionState.value = status
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
                // `true` literally, not `inReplay` — the span opens on the next line.
                session.emitOutput(CLEAR_BEFORE_REPLAY, CLEAR_BEFORE_REPLAY.size, true)
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
                    // The replay is sunk cost already paid. Charging it would
                    // make the first live byte after any reconnect-while-
                    // unwatched blow the budget instantly, suspend again, loop.
                    unwatchedBytes = 0
                    enterLiveState()
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
        enqueueInput(bytes)
    }

    /** Queue [bytes] for the PTY as an `'i'` (input) frame — the raw-byte path shared by
     *  [sendBytes] and [sendTerminalBytes]. */
    private fun enqueueInput(bytes: ByteArray) {
        sendChannel.trySend(TerminalProtocol.encodeInput(bytes))
    }

    /**
     * Bytes originating from the terminal surface itself: keys typed while it holds the
     * IME, an ActionMode paste, and the emulator's automatic replies (DA, DSR/CPR,
     * DECRQM, ...) to escape sequences it parsed out of inbound output.
     *
     * Gated on Live AND a running session (mirrors [sendTappedRow] plus the visible
     * controls' running gate): an IME raised on the terminal surface must not keep
     * feeding a PTY that stopped or crashed while the WS stayed connected, and
     * auto-replies to sequences re-parsed from the replay ring must not reach the live
     * PTY as stale duplicates.
     *
     * A reply the emulator generated while parsing inbound output still goes on the
     * wire, but skips the follow-mode resume: that is the program answering its own
     * query, not the user typing, so letting it run `setFollowMode(true)` would let a
     * bare cursor-position report (`ESC[6n`, which prompt frameworks like powerlevel10k
     * and starship emit routinely) yank a paused viewport back to the tail and wipe the
     * unread tally — the exact hijack follow mode exists to prevent. Mirrors iOS
     * `TerminalSessionModel.sendTerminalBytes`.
     */
    private fun sendTerminalBytes(bytes: ByteArray) {
        if (_connectionState.value != ConnectionState.Live ||
            _sessionState.value != "running"
        ) {
            return
        }
        if (session.isApplyingEmulatorOutput) {
            enqueueInput(bytes)
            return
        }
        sendBytes(bytes)
    }

    private suspend fun loadShortcutRecents(kindKey: String) {
        _shortcutRecents.value = preferencesStore.getRecentShortcutChords(kindKey)
            .mapNotNull(KeyChord::parse)
    }

    /** Pending recents load; cancel-and-replace so reopening the picker while
     *  a previous open is still awaiting kind resolution never leaks a
     *  suspended collector or double-writes [_shortcutRecents]. */
    private var shortcutRecentsLoadJob: Job? = null

    /** Fired when the shortcut picker opens (lazy — see [shortcutRecents]).
     *  Suspends until the kind resolves rather than no-opping: the picker can
     *  open in the sub-second window before the session list / REST seed
     *  names the kind, and a one-shot early return would leave the MRU row
     *  empty for the rest of that open while the saved-shortcut list (a
     *  combine over [resolvedKind]) self-heals. */
    fun refreshShortcutRecents() {
        shortcutRecentsLoadJob?.cancel()
        shortcutRecentsLoadJob = viewModelScope.launch {
            val kindKey = sessionKind.filterNotNull().first().wireName
            runCatching { loadShortcutRecents(kindKey) }
        }
    }

    /** Encode a chord and send it down the raw-byte path. The send is
     *  unconditional; MRU persistence is a UX nicety that must never block or
     *  fail the send, so it runs fire-and-forget afterwards — and is skipped
     *  entirely while the kind is unresolved rather than recorded under a
     *  guessed bucket. */
    fun sendChord(chord: KeyChord) {
        val encoding = KeyChordEncoder.encode(chord) ?: return
        sendBytes(encoding.bytes)
        val kindKey = sessionKind.value?.wireName ?: return
        viewModelScope.launch {
            runCatching {
                preferencesStore.addRecentShortcutChord(kindKey, chord.token())
                loadShortcutRecents(kindKey)
            }
        }
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

    /** Single entry point into [ConnectionState.Live]. */
    private fun enterLiveState() {
        _connectionState.value = ConnectionState.Live
        // Reaching live retires the latch: an ON_START that never arrived
        // must not suppress a later, genuine screen-on nudge.
        resumedFromSuspend = false
        resendCachedSize()
    }

    /**
     * Re-assert the last measured grid to the agent: on every entry into Live,
     * and as the delivery step of every [scheduleSizeReassert] debounce.
     *
     * The PTY is spawned at the create default (24x80) and only a resize frame
     * corrects it. The other resize trigger — a layout pass (TerminalScreen's
     * onResize) — can settle before the WS connects (common when the agent host is
     * slow to attach, e.g. a macOS agent behind a TCC prompt); that frame is dropped
     * by the transport and then suppressed by [resize]'s dedupe, leaving the PTY at
     * 24 rows so a full-screen TUI fills only the top of a taller device grid. Force
     * past the debounce and dedupe here so the size still reaches a freshly spawned
     * or agent-spawned (Auto Scan) PTY. Mirrors iOS TerminalSessionModel.resendCachedSize().
     */
    private fun resendCachedSize() {
        if (lastResizeRows <= 0 || lastResizeCols <= 0) return
        sendChannel.trySend(TerminalProtocol.encodeResize(lastResizeRows, lastResizeCols))
    }

    /** Reveal a hidden composer and focus its field. The focus request also retargets
     *  an IME that is still focused on the terminal surface from an earlier raise, so
     *  keystrokes stop routing straight into the PTY once the field is back. */
    fun revealComposer() {
        _composerVisible.value = true
        _requestInputFocus.value = true
    }

    /** Collapse the composer ("hide command field"). Sticks until [revealComposer].
     *  No focus or raise side effect: the composer unmounting dismisses the IME
     *  naturally, and the user resumes typing via the keyboard button in the control row. */
    fun hideComposer() {
        _composerVisible.value = false
    }

    /** Replace the input bar's contents with [text] and place the cursor at the
     *  end. Used for "tap a row to copy into the input" (`onRowCopyToInput`).
     *  Reveals a hidden composer first — the focus request needs the field to be
     *  composed, and the user's gesture explicitly targets it. */
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

    /** Deterministic fetch fired when the shortcut picker opens, mirroring
     *  [refreshTemplates]: the shared cache is otherwise only populated
     *  best-effort (WS-connect refresh in UserEventBus). */
    fun refreshSavedShortcuts() {
        if (_savedShortcutsLoading.value) return
        _savedShortcutsLoading.value = true
        viewModelScope.launch {
            try {
                shortcutsRepository.refreshShortcuts()
            } catch (t: Throwable) {
                _userMessages.tryEmit(
                    "Failed to load saved shortcuts: ${t.message ?: "unknown error"}",
                )
            } finally {
                _savedShortcutsLoading.value = false
            }
        }
    }

    /** The session's actual row, or null while it cannot be determined.
     *  Persisting a synced shortcut under a guessed kind would file it in
     *  the wrong per-kind bucket permanently (unlike the self-healing
     *  device-local recents) — so saves re-resolve from the authoritative
     *  sources instead of trusting the [sessionKind] StateFlow value.
     *
     *  The live list wins on both sides of the fetch. A sessions.changed
     *  snapshot landing while the request is in flight is newer than the
     *  response by definition, and [headerLabel] reads a mutable field off this
     *  row — returning the REST copy there would regress the title to whatever
     *  it was when the request was issued, until the next session event. */
    private suspend fun resolvedSession(): SessionResponse? {
        liveSessionRow()?.let { return it }
        val fetched = runCatching { sessionRepository.listSessions() }.getOrNull()
            ?.firstOrNull { it.session_id == sessionId }
        return liveSessionRow() ?: fetched
    }

    private fun liveSessionRow(): SessionResponse? =
        sessionRepository.observeSessionList().value
            .firstOrNull { it.session_id == sessionId }

    /** Chord tokens with a create request currently in flight. Main-thread
     *  confined: [saveShortcut] is only ever called from the UI, and
     *  `viewModelScope` dispatches on Main. */
    private val savesInFlight = mutableSetOf<String>()

    /** Pin a chord as a button for this session's kind, unnamed — the picker
     *  routes naming through [renameShortcut] afterwards. Re-saving a chord
     *  that is already live is a server-side no-op rather than a duplicate.
     *
     *  Concurrent saves of the SAME chord are dropped, and that is load-bearing
     *  rather than mere request thrift. The server's upsert probes live rows
     *  only, so a second create that reaches it AFTER an interleaved delete
     *  finds nothing and inserts a fresh row — resurrecting a chord the user
     *  had just removed. The picker cannot self-limit here: its toggle only
     *  flips once the response lands, so it stays reading "Save" for the whole
     *  round trip and a double tap is ordinary.
     *
     *  Success is deliberately silent: the picker derives its "Saved" state
     *  from the repository cache, so the confirmation is the UI itself, and a
     *  snackbar would only duplicate it. */
    fun saveShortcut(chord: KeyChord) {
        val token = chord.token()
        if (!savesInFlight.add(token)) return
        viewModelScope.launch {
            try {
                val kind = resolvedSession()?.kind
                if (kind == null) {
                    _userMessages.tryEmit(
                        "Failed to save shortcut: session type not known yet",
                    )
                    return@launch
                }
                // Publish what this save resolved. savedShortcutsForKind renders
                // empty until resolvedKind lands, and this call races the sessionKind
                // merge that normally writes it — so without this a cold-launch save
                // can succeed while the picker still shows "Save" with no hint row
                // and (success being silent) no feedback at all. Same authority as
                // the merge: resolvedSession() never returns the eager seed.
                resolvedKind.compareAndSet(null, kind)
                try {
                    shortcutsRepository.createShortcut(kind.wireName, chord.token(), null)
                } catch (t: Throwable) {
                    _userMessages.tryEmit(
                        "Failed to save shortcut: ${t.message ?: "unknown error"}",
                    )
                }
            } finally {
                // Always clears — unlike a UI-side pending flag, which has no
                // signal to clear it on when the request fails.
                savesInFlight.remove(token)
            }
        }
    }

    /** Rename a saved shortcut. A blank [name] clears the custom name so the
     *  button falls back to the chord label. */
    fun renameShortcut(shortcutId: String, name: String) {
        viewModelScope.launch {
            try {
                shortcutsRepository.renameShortcut(shortcutId, name.trim())
            } catch (_: Throwable) {
                _userMessages.tryEmit("Failed to rename shortcut")
            }
        }
    }

    /** Unpin a saved shortcut.
     *
     *  DELETE is not idempotent server-side: the ownership probe filters
     *  soft-deleted rows, so deleting an already-deleted shortcut answers 403
     *  rather than no-op'ing. The picker's Saved toggle carries no in-flight
     *  state and stays tappable for the whole round trip, so a second tap is
     *  ordinary — and reporting "Failed to remove" for a shortcut that IS
     *  removed would be a lie on the one surface built to be trusted. Ids only
     *  ever come from this user's own cached list, so 403/404 can only mean
     *  "already gone": reconcile the cache and stay silent. */
    fun removeShortcut(shortcutId: String) {
        viewModelScope.launch {
            try {
                shortcutsRepository.deleteShortcut(shortcutId)
            } catch (t: Throwable) {
                val alreadyGone = t is ApiException && (t.code == 403 || t.code == 404)
                if (alreadyGone) {
                    shortcutsRepository.applyDeleted(shortcutId)
                } else {
                    _userMessages.tryEmit("Failed to remove shortcut")
                }
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
        screenVisible = true
        syncWatched()
    }

    fun markScreenHidden() {
        foregroundSessionTracker.clearVisible(sessionId)
        screenVisible = false
        syncWatched()
    }

    /**
     * A session is watched only while the app is on screen *and* this terminal
     * is composed. The two halves are tracked separately because neither
     * implies the other: backgrounding leaves the composable in place, and
     * navigating forward disposes it without backgrounding the app.
     *
     * A session-scoped child screen counts as composed: it disposes this
     * terminal exactly like leaving would, but the user is still inside the
     * session and comes straight back, so charging that visit with a socket
     * close and a full ring replay would be the worst trade the governor can
     * make. The child holds the watch only while it is itself composed (see
     * [ForegroundSessionTracker.watchHolds]), so the flag cannot outlive it.
     * Backgrounding still counts — the foreground term is unchanged.
     */
    private fun syncWatched() {
        if ((screenVisible || heldByChild) && appForeground) {
            unwatchedSinceMs = null
            unwatchedBytes = 0
            resumeStream()
            return
        }
        // Re-baselined on the transition, not on suspend: bytes spent while the
        // user was reading are not the background's to answer for, and without
        // this a long-lived session would carry its whole history into the
        // first hide and suspend instantly.
        if (unwatchedSinceMs == null) {
            unwatchedSinceMs = System.currentTimeMillis()
            unwatchedBytes = 0
        }
    }

    private fun chargeInbound(bytes: Long) {
        if (streamSuspended) return
        val since = unwatchedSinceMs ?: return
        unwatchedBytes += bytes
        // Never mid-replay: tearing one down wastes bytes already paid for and
        // leaves the overlay pinned on a span whose terminator never arrives.
        if (inReplay) return
        if (suspendPolicy(System.currentTimeMillis() - since, unwatchedBytes)) {
            suspendStream()
        }
    }

    /**
     * Stop consuming this session's PTY output.
     *
     * Closing the socket is the only lever — the session protocol has no pause
     * frame. Detaching is free on the agent: its `_handle_mobile_detach` only
     * logs, the PTY keeps running and the ring keeps filling, so the next open
     * replays everything missed.
     *
     * [WebSocketConnection.pause] rather than `disconnect`, which would end the
     * connection's scope and make the resume a silent no-op.
     */
    private fun suspendStream() {
        if (streamSuspended) return
        streamSuspended = true
        inReplay = false
        connection?.pause()
        _isConnected.value = false
        _connectionState.value = ConnectionState.Suspended
    }

    private fun resumeStream() {
        if (!streamSuspended) return
        streamSuspended = false
        resumedFromSuspend = true
        unwatchedBytes = 0
        // Explicitly false, not just left alone: the send gates read this flag
        // on its own, and it has to stay shut across the whole reconnect and
        // replay window until the new socket's `Connected` reopens it.
        _isConnected.value = false
        _connectionState.value = ConnectionState.Reconnecting
        connection?.connect()
    }

    /**
     * Wake the session WebSocket up when the terminal screen becomes
     * visible again — typically after the device screen turned back on or
     * the app returned from background. While the screen was off the WS may
     * have dropped (Doze restricts network) and the exponential backoff
     * inside [WebSocketConnection.scheduleReconnect] can be sitting at the
     * 30 s ceiling, so without this nudge the user can stare at the
     * "Reconnecting…" overlay for up to half a minute. When the connection
     * is already [ConnectionState.Live] the reconnect is skipped — forcing
     * one on a healthy socket would burn a server replay round-trip for
     * nothing and flash the overlay unnecessarily — but the cached PTY size
     * is re-asserted: another client may have resized the shared PTY while
     * this one was unwatched, and the local dedupe cache cannot know.
     */
    fun onScreenVisible() {
        // The watch gate owns the reconnect while suspended, and
        // `forceReconnect` would no-op anyway: `pause()` cleared
        // `shouldReconnect`.
        if (streamSuspended) return
        // Foregrounding fires both paths: the tracker's collector resumes the
        // stream, and the activity's ON_START lands here. By then the resume
        // has cleared `streamSuspended`, so without this latch the nudge would
        // force-reconnect the socket that resume just opened — a second
        // handshake and a second full ring replay, fanned out to every other
        // client attached to the session, on the one path this exists to make
        // cheap.
        if (resumedFromSuspend) {
            resumedFromSuspend = false
            return
        }
        if (_connectionState.value == ConnectionState.Live) {
            // Live-only on purpose: every non-Live state re-asserts on its own
            // entry into Live via enterLiveState(), including a resume from
            // suspension, so this edge is the one case nothing else covers. The
            // kernel drops a same-size TIOCSWINSZ before SIGWINCH, so a
            // redundant re-assert costs one 5-byte frame and repaints nothing.
            if (lastResizeRows > 0 && lastResizeCols > 0) scheduleSizeReassert()
            return
        }
        connection?.forceReconnect()
    }

    fun resize(rows: Int, cols: Int) {
        if (rows == lastResizeRows && cols == lastResizeCols) return
        lastResizeRows = rows
        lastResizeCols = cols
        // TerminalView.updateSize() already calls session.updateSize(...) with real
        // renderer metrics synchronously before this callback fires; calling it again here
        // with stub (12, 24) metrics would clobber the correct values.
        scheduleSizeReassert()
    }

    /**
     * Debounced send of the cached grid with no dedupe consult. The single
     * [resizeJob] is what coalesces an edge assert with a concurrent layout
     * pass into one frame, and the cache is read at fire time so the last
     * re-arm wins with the newest grid.
     */
    private fun scheduleSizeReassert() {
        resizeJob?.cancel()
        resizeJob = viewModelScope.launch {
            delay(100)
            resendCachedSize()
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
