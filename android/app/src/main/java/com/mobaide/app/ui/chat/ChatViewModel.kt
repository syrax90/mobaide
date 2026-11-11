package com.mobaide.app.ui.chat

import android.net.Uri
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.chat.ChatConfigOption
import com.mobaide.app.data.chat.ChatFrame
import com.mobaide.app.data.chat.ChatImageEncoder
import com.mobaide.app.data.chat.ChatProtocol
import com.mobaide.app.data.chat.ChatReduction
import com.mobaide.app.data.chat.ChatRequest
import com.mobaide.app.data.chat.ChatSessionCache
import com.mobaide.app.data.chat.ChatStatus
import com.mobaide.app.data.chat.ChatSyncRequest
import com.mobaide.app.data.chat.ChatTimeline
import com.mobaide.app.data.chat.ChatTimelineReducer
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.data.repository.toGitCapabilities
import com.mobaide.app.data.review.HealthyTimeMeter
import com.mobaide.app.data.review.ReviewPromptCoordinator
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.AttentionNotifier
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.ui.common.insertAtCursor
import com.mobaide.app.ui.sessions.sessionKindLabel
import com.mobaide.app.ui.voice.withTranscript
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.shareIn
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okio.ByteString
import org.json.JSONObject
import java.security.SecureRandom
import javax.inject.Inject

/** The session's row facts the chat top bar and ⋯ menu need. */
data class ChatSessionHeader(
    val title: String = "",
    val kindLabel: String = "",
    /** Null until the session row arrives. */
    val kind: SessionKind? = null,
    val cwd: String? = null,
    val projectName: String? = null,
)

/**
 * One chat session's screen: the per-session socket (`'c'` commands out, `'a'`
 * frames in), the timeline reducer, the composer and the in-flight commands.
 *
 * Every command carries `<clientId>-<n>`; results for ids this screen did not
 * send come from other clients of the same session and are ignored.
 */
@HiltViewModel
class ChatViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val sessionRepository: SessionRepository,
    customAgentTypesRepository: CustomAgentTypesRepository,
    private val templatesRepository: TemplatesRepository,
    private val foregroundSessionTracker: ForegroundSessionTracker,
    private val appForegroundTracker: AppForegroundTracker,
    private val attentionNotifier: AttentionNotifier,
    private val chatCache: ChatSessionCache,
    private val imageEncoder: ChatImageEncoder,
    private val reviewPromptCoordinator: ReviewPromptCoordinator,
    private val preferencesStore: PreferencesStore,
) : ViewModel() {

    val sessionId: String = savedStateHandle["sessionId"]
        ?: throw IllegalArgumentException("sessionId required")

    private val clientId: String = ByteArray(4).also(SecureRandom()::nextBytes)
        .joinToString("") { "%02x".format(it) }
    private var commandCounter = 0

    private var connection: WebSocketConnection? = null
    private val reducer = ChatTimelineReducer(chatCache.get(sessionId))

    /** Frames are reduced off the main thread; open, close and frames must not interleave. */
    private val reducerLock = Mutex()

    private sealed interface PendingCommand {
        data class Prompt(val text: String, val images: List<ChatComposerImage>) : PendingCommand
        data class Config(val configId: String) : PendingCommand
        data class Respond(val requestId: String) : PendingCommand
        data class Simple(val failure: String?) : PendingCommand
        data class Queue(val queueId: String, val failure: String?) : PendingCommand
        data object Restart : PendingCommand
    }

    private class ConfigOverride(val value: JsonPrimitive, var confirmed: Boolean = false)

    /** Insertion-ordered, so prompts a dropped socket lost go back into the draft in send order. */
    private val pending = LinkedHashMap<String, PendingCommand>()
    private val configOverrides = LinkedHashMap<String, ConfigOverride>()

    private val _ui = MutableStateFlow(initialUi())
    val uiState: StateFlow<ChatUiState> = _ui.asStateFlow()

    private val _composer = MutableStateFlow(TextFieldValue(""))
    val composer: StateFlow<TextFieldValue> = _composer.asStateFlow()

    private val _messages = Channel<String>(Channel.BUFFERED)
    val messages: Flow<String> = _messages.receiveAsFlow()

    private val _sessionEnded = Channel<Unit>(Channel.BUFFERED)

    /** Fires once when the agent proves this session never existed while on screen. */
    val sessionEnded: Flow<Unit> = _sessionEnded.receiveAsFlow()

    private var everSeenAlive = false
    private var sessionEndedEmitted = false
    private var paused = false
    private var pauseJob: Job? = null
    private var nextImageId = 0L

    private var screenVisible = false
    private var appForeground = true

    /** `screenVisible && appForeground` as a flow, so the meter's collector sees both halves. */
    private val inUse = MutableStateFlow(false)

    /** Healthy use of this visit, for the review prompt. */
    private val reviewMeter = HealthyTimeMeter()

    /** The user stopped this session, so leaving it is a clean end rather than a failure. */
    private var endedByUser = false

    private val sessionRow: Flow<SessionResponse> =
        merge(
            sessionRepository.observeSessionList()
                .mapNotNull { sessions -> sessions.firstOrNull { it.session_id == sessionId } },
            flow { resolvedSession()?.let { emit(it) } },
        ).shareIn(viewModelScope, SharingStarted.Eagerly, replay = 1)

    val header: StateFlow<ChatSessionHeader> =
        combine(sessionRow, customAgentTypesRepository.observeTypes()) { session, types ->
            val kindLabel = sessionKindLabel(session, types.associate { "custom_${it.id}" to it.name })
            ChatSessionHeader(
                title = session.title?.takeIf { it.isNotBlank() } ?: kindLabel,
                kindLabel = kindLabel,
                kind = session.kind,
                cwd = session.cwd,
                projectName = session.project_name,
            )
        }.stateIn(viewModelScope, SharingStarted.Eagerly, ChatSessionHeader())

    /** The agent's `capabilities.git`, which gates the ⋯ menu's Git entries as on the terminal. */
    val git: StateFlow<GitCapabilities> =
        flow {
            emit(
                runCatching { sessionRepository.getAgentVersion().toGitCapabilities() }
                    .getOrDefault(GitCapabilities.NONE),
            )
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), GitCapabilities.NONE)

    /** Focus view: the timeline folds thinking and tool calls (one setting for every chat). */
    val focusView: StateFlow<Boolean> =
        preferencesStore.chatFocusView.stateIn(viewModelScope, SharingStarted.Eagerly, true)

    val templates: StateFlow<List<TemplateDto>> = templatesRepository.observeTemplates()

    private val _templatesLoading = MutableStateFlow(false)
    val templatesLoading: StateFlow<Boolean> = _templatesLoading.asStateFlow()

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
                    } else if (synced && everSeenAlive) {
                        // Left the agent's list while open: closed, timeline kept.
                        _ui.update { it.copy(sessionClosed = true, answering = emptyMap()) }
                    }
                }
        }
        viewModelScope.launch {
            appForegroundTracker.isForeground.collect { foreground ->
                appForeground = foreground
                inUse.value = screenVisible && appForeground
                if (foreground) resumeSocket() else schedulePause()
            }
        }
        viewModelScope.launch {
            combine(inUse, _ui) { inUse, ui -> inUse && ui.isHealthy }
                .distinctUntilChanged()
                .collect { healthy -> reviewMeter.update(healthy) }
        }
        connect()
    }

    private fun initialUi(): ChatUiState {
        val cached = reducer.timeline ?: return ChatUiState()
        return ChatUiState(
            loaded = true,
            items = cached.items,
            truncated = cached.truncated,
            state = cached.state,
            config = cached.state.config,
        )
    }

    private fun connect() {
        val created = sessionRepository.createSessionIoConnection(sessionId)
        connection = created
        // Subscribed before connect(): the open and the first frames must not be missed.
        viewModelScope.launch(start = CoroutineStart.UNDISPATCHED) {
            created.events.collect { event ->
                when (event) {
                    WsEvent.Connected -> onSocketOpen()
                    WsEvent.Disconnected, is WsEvent.Error -> onSocketDown()
                    is WsEvent.Binary -> onBinary(event.bytes)
                    is WsEvent.Message -> Unit
                }
            }
        }
        created.connect()
    }

    private suspend fun onSocketOpen() {
        if (paused) return
        _ui.update { it.copy(live = true) }
        sendSync(reducerLock.withLock { reducer.onOpen() })
    }

    private suspend fun onSocketDown() {
        reducerLock.withLock { reducer.onClose() }
        // A prompt the dead socket took has no result coming on the next one.
        val lostPrompts = pending.values.filterIsInstance<PendingCommand.Prompt>()
        pending.clear()
        if (lostPrompts.isNotEmpty()) {
            lostPrompts.asReversed().forEach(::restoreDraft)
            _messages.trySend("The connection dropped. Check that the message was sent.")
        }
        configOverrides.clear()
        _ui.update { ui ->
            ui.copy(
                live = false,
                answering = emptyMap(),
                config = ui.state.config,
                restarting = false,
                queueBusy = emptySet(),
            )
        }
    }

    private suspend fun onBinary(bytes: ByteString) {
        if (paused || bytes.size == 0) return
        if (!_ui.value.live) onSocketOpen()
        when (bytes[0]) {
            ChatProtocol.TYPE_FRAME -> {
                val outcome = reducerLock.withLock {
                    withContext(Dispatchers.Default) {
                        when (val frame = ChatProtocol.decode(bytes)) {
                            is ChatFrame.Snapshot -> Reduced(frame, reducer.apply(frame), reducer.timeline)
                            is ChatFrame.Events -> Reduced(frame, reducer.apply(frame), reducer.timeline)
                            is ChatFrame.Result -> Reduced(frame, ChatReduction.NONE, null)
                            null -> null
                        }
                    }
                } ?: return
                val frame = outcome.frame
                if (frame is ChatFrame.Result) {
                    onResult(frame)
                    return
                }
                if (frame is ChatFrame.Snapshot) configOverrides.clear()
                outcome.reduction.sync?.let(::sendSync)
                if (outcome.reduction.changed || frame is ChatFrame.Snapshot) {
                    outcome.timeline?.let { publish(it, outcome.reduction.stateChanged) }
                }
            }
            TerminalProtocol.TYPE_STATE -> {
                val status = runCatching {
                    JSONObject(bytes.substring(1).utf8()).optString("status")
                }.getOrNull()
                if (status == "running" || status == "starting") everSeenAlive = true
                if (status == "stopped" || status == "crashed") {
                    _ui.update { it.copy(sessionClosed = true, answering = emptyMap()) }
                }
            }
            else -> Unit
        }
    }

    private class Reduced(val frame: ChatFrame, val reduction: ChatReduction, val timeline: ChatTimeline?)

    private fun publish(timeline: ChatTimeline, stateChanged: Boolean) {
        chatCache.put(sessionId, timeline)
        val state = timeline.state
        if (stateChanged) {
            val previous = _ui.value.state.config
            val configChanged = previous != state.config
            val iterator = configOverrides.entries.iterator()
            while (iterator.hasNext()) {
                val (id, override) = iterator.next()
                val option = state.config.firstOrNull { it.id == id }
                val agrees = option != null && option.currentValue() == override.value
                if (option == null || agrees || (override.confirmed && configChanged)) iterator.remove()
            }
        }
        val requestIds = state.requests.mapTo(HashSet()) { it.id }
        val queueIds = state.queue.mapTo(HashSet()) { it.id }
        _ui.update { ui ->
            ui.copy(
                loaded = true,
                items = timeline.items,
                truncated = timeline.truncated,
                state = state,
                config = effectiveConfig(state.config),
                answering = ui.answering.filterKeys { it in requestIds },
                requestErrors = ui.requestErrors.filterKeys { it in requestIds },
                formDrafts = ui.formDrafts.filterKeys { it in requestIds },
                restarting = ui.restarting && state.status == ChatStatus.ERROR,
                queueBusy = ui.queueBusy.filterTo(HashSet()) { it in queueIds },
            )
        }
    }

    private fun effectiveConfig(config: List<ChatConfigOption>): List<ChatConfigOption> {
        if (configOverrides.isEmpty()) return config
        return config.map { option ->
            val override = configOverrides[option.id] ?: return@map option
            if (option.isBoolean) {
                option.copy(boolValue = override.value.content.toBooleanStrictOrNull() ?: option.boolValue)
            } else {
                option.copy(selectValue = override.value.content)
            }
        }
    }

    private fun ChatConfigOption.currentValue(): JsonPrimitive =
        if (isBoolean) JsonPrimitive(boolValue) else JsonPrimitive(selectValue)

    private fun onResult(result: ChatFrame.Result) {
        val command = pending.remove(result.id) ?: return
        when (command) {
            is PendingCommand.Prompt -> if (!result.ok) {
                restoreDraft(command)
                _messages.trySend(result.error ?: "The message was not sent")
            }
            is PendingCommand.Config -> {
                val override = configOverrides[command.configId]
                if (result.ok) {
                    override?.confirmed = true
                } else {
                    configOverrides.remove(command.configId)
                    _messages.trySend(result.error ?: "The setting was not changed")
                }
                _ui.update { it.copy(config = effectiveConfig(it.state.config)) }
            }
            is PendingCommand.Respond -> _ui.update { ui ->
                val failed = !result.ok && result.error != ALREADY_ANSWERED
                ui.copy(
                    answering = ui.answering - command.requestId,
                    requestErrors = if (failed) {
                        ui.requestErrors + (command.requestId to (result.error ?: "The answer was not accepted"))
                    } else {
                        ui.requestErrors - command.requestId
                    },
                )
            }
            is PendingCommand.Simple -> if (!result.ok && command.failure != null) {
                reviewPromptCoordinator.noteError()
                _messages.trySend(result.error ?: command.failure)
            }
            is PendingCommand.Queue -> {
                _ui.update { it.copy(queueBusy = it.queueBusy - command.queueId) }
                // A double tap, or a turn that just ended, makes this harmless.
                if (!result.ok && command.failure != null && result.error != ALREADY_SENT) {
                    reviewPromptCoordinator.noteError()
                    _messages.trySend(result.error ?: command.failure)
                }
            }
            PendingCommand.Restart -> {
                _ui.update { it.copy(restarting = false) }
                if (!result.ok) _messages.trySend(result.error ?: "Could not restart the agent")
            }
        }
    }

    private fun restoreDraft(prompt: PendingCommand.Prompt) {
        val current = _composer.value.text
        val restored = if (current.isBlank()) prompt.text else prompt.text + "\n" + current
        _composer.value = TextFieldValue(restored, TextRange(restored.length))
        if (_ui.value.images.isEmpty() && prompt.images.isNotEmpty()) {
            _ui.update { it.copy(images = prompt.images) }
        }
    }

    private fun nextCommandId(): String {
        commandCounter += 1
        return "$clientId-$commandCounter"
    }

    private fun send(command: JsonObject): Boolean =
        connection?.sendBinary(ChatProtocol.encode(command)) == true

    private fun sendSync(request: ChatSyncRequest) {
        val id = nextCommandId()
        val command = when (request) {
            ChatSyncRequest.Snapshot -> ChatProtocol.sync(id, null, null)
            is ChatSyncRequest.Resume -> ChatProtocol.sync(id, request.epoch, request.afterSeq)
        }
        send(command)
    }

    fun updateComposer(value: TextFieldValue) {
        _composer.value = value
    }

    fun insertTranscript(transcript: String) {
        _composer.value = _composer.value.withTranscript(transcript) ?: return
    }

    fun sendPrompt() {
        val ui = _ui.value
        // A message sent while images are still being read would leave them
        // for the next one.
        if (!ui.canAct || ui.imagesLoading) return
        val text = _composer.value.text.trim()
        val images = ui.images
        if (text.isEmpty() && images.isEmpty()) return
        if (text.length > PROMPT_TEXT_MAX) {
            _messages.trySend("The message is too long")
            return
        }
        val id = nextCommandId()
        if (!send(ChatProtocol.prompt(id, text, images.map { it.payload }))) {
            _messages.trySend("Not connected. Try again.")
            return
        }
        pending[id] = PendingCommand.Prompt(text, images)
        _composer.value = TextFieldValue("")
        if (images.isNotEmpty()) _ui.update { it.copy(images = emptyList()) }
    }

    fun stop() {
        if (!_ui.value.canAct) return
        sendSimple("Could not stop the turn") { id -> ChatProtocol.cancel(id) }
    }

    fun restart() {
        if (!_ui.value.canRestart) return
        val id = nextCommandId()
        if (!send(ChatProtocol.restart(id))) {
            _messages.trySend("Not connected. Try again.")
            return
        }
        pending[id] = PendingCommand.Restart
        _ui.update { it.copy(restarting = true) }
    }

    fun dequeue(queueId: String) = sendQueueCommand(queueId, "Could not remove the message") { id ->
        ChatProtocol.dequeue(id, queueId)
    }

    fun steer(queueId: String) = sendQueueCommand(queueId, "Could not send the message now") { id ->
        ChatProtocol.steer(id, queueId)
    }

    private fun sendQueueCommand(queueId: String, failure: String?, build: (String) -> JsonObject) {
        val ui = _ui.value
        if (!ui.live || queueId in ui.queueBusy) return
        val id = nextCommandId()
        if (!send(build(id))) {
            if (failure != null) _messages.trySend("Not connected. Try again.")
            return
        }
        pending[id] = PendingCommand.Queue(queueId, failure)
        _ui.update { it.copy(queueBusy = it.queueBusy + queueId) }
    }

    private fun sendSimple(failure: String?, build: (String) -> JsonObject) {
        val id = nextCommandId()
        if (send(build(id))) {
            pending[id] = PendingCommand.Simple(failure)
        } else if (failure != null) {
            reviewPromptCoordinator.noteError()
            _messages.trySend("Not connected. Try again.")
        }
    }

    fun setConfig(option: ChatConfigOption, value: String) = setConfigValue(option, JsonPrimitive(value))

    fun setConfig(option: ChatConfigOption, value: Boolean) = setConfigValue(option, JsonPrimitive(value))

    private fun setConfigValue(option: ChatConfigOption, value: JsonPrimitive) {
        if (!_ui.value.canAct) return
        val id = nextCommandId()
        if (!send(ChatProtocol.setConfig(id, option.id, value))) {
            _messages.trySend("Not connected. Try again.")
            return
        }
        pending[id] = PendingCommand.Config(option.id)
        configOverrides[option.id] = ConfigOverride(value)
        _ui.update { it.copy(config = effectiveConfig(it.state.config)) }
    }

    fun respondPermission(request: ChatRequest.Permission, optionId: String) {
        val ui = _ui.value
        if (!ui.live || ui.sessionClosed || request.id in ui.answering) return
        val id = nextCommandId()
        if (!send(ChatProtocol.respondPermission(id, request.id, optionId))) {
            _messages.trySend("Not connected. Try again.")
            return
        }
        pending[id] = PendingCommand.Respond(request.id)
        _ui.update {
            it.copy(answering = it.answering + (request.id to optionId), requestErrors = it.requestErrors - request.id)
        }
    }

    fun updateFormValue(request: ChatRequest.Form, key: String, value: ChatFormValue) {
        _ui.update { ui ->
            val draft = ui.formDrafts[request.id] ?: initialFormDraft(request.fields)
            ui.copy(formDrafts = ui.formDrafts + (request.id to draft + (key to value)))
        }
    }

    fun respondForm(request: ChatRequest.Form, action: String) {
        val ui = _ui.value
        if (!ui.live || ui.sessionClosed || request.id in ui.answering) return
        val values = if (action == FORM_ACCEPT) {
            formValues(request, ui.formDrafts[request.id].orEmpty()) ?: return
        } else {
            null
        }
        val id = nextCommandId()
        if (!send(ChatProtocol.respondForm(id, request.id, action, values))) {
            _messages.trySend("Not connected. Try again.")
            return
        }
        pending[id] = PendingCommand.Respond(request.id)
        _ui.update {
            it.copy(answering = it.answering + (request.id to action), requestErrors = it.requestErrors - request.id)
        }
    }

    fun startSignIn(request: ChatRequest.SignIn, methodId: String) =
        sendSignIn(request, signInStartKey(methodId)) { id -> ChatProtocol.signInStart(id, methodId) }

    fun submitSignIn(request: ChatRequest.SignIn, value: String) {
        val trimmed = value.trim()
        if (trimmed.isEmpty() || request.busy) return
        sendSignIn(request, SIGN_IN_SUBMIT) { id -> ChatProtocol.signInSubmit(id, trimmed) }
    }

    fun cancelSignIn(request: ChatRequest.SignIn) =
        sendSignIn(request, SIGN_IN_CANCEL) { id -> ChatProtocol.signInCancel(id) }

    private fun sendSignIn(request: ChatRequest.SignIn, action: String, build: (String) -> JsonObject) {
        val ui = _ui.value
        if (!ui.live || ui.sessionClosed || request.id in ui.answering) return
        val id = nextCommandId()
        if (!send(build(id))) {
            _messages.trySend("Not connected. Try again.")
            return
        }
        pending[id] = PendingCommand.Respond(request.id)
        _ui.update {
            it.copy(answering = it.answering + (request.id to action), requestErrors = it.requestErrors - request.id)
        }
    }

    fun addImages(uris: List<Uri>) {
        if (_ui.value.imagesLoading) return
        val room = ChatImageEncoder.MAX_IMAGES - _ui.value.images.size
        if (room <= 0) {
            _messages.trySend("Attach at most ${ChatImageEncoder.MAX_IMAGES} images")
            return
        }
        if (uris.isEmpty()) return
        if (uris.size > room) _messages.trySend("Attach at most ${ChatImageEncoder.MAX_IMAGES} images")
        _ui.update { it.copy(imagesLoading = true) }
        viewModelScope.launch {
            var failed = 0
            var tooLarge = false
            for (uri in uris.take(room)) {
                // Send waits for the batch, so a read that hangs (a cloud photo
                // offline) must not block it for good. A blocking read ignores
                // cancellation: await it with a timeout and drop a late result.
                val pending = async { imageEncoder.encode(uri) }
                val encoded = withTimeoutOrNull(IMAGE_LOAD_TIMEOUT_MS) { pending.await() }
                if (encoded == null) pending.cancel()
                if (encoded == null) {
                    failed += 1
                    continue
                }
                val total = _ui.value.images.sumOf { it.payload.data.length } + encoded.payload.data.length
                if (total > IMAGES_TOTAL_MAX) {
                    tooLarge = true
                    continue
                }
                val image = ChatComposerImage(
                    id = nextImageId++,
                    payload = encoded.payload,
                    thumbnail = encoded.thumbnail.asImageBitmap(),
                )
                _ui.update { it.copy(images = it.images + image) }
            }
            _ui.update { it.copy(imagesLoading = false) }
            when {
                tooLarge -> _messages.trySend("These images are too large to send together")
                failed > 0 -> _messages.trySend("Could not read the image")
            }
        }
    }

    fun removeImage(id: Long) {
        _ui.update { ui -> ui.copy(images = ui.images.filterNot { it.id == id }) }
    }

    fun refreshTemplates() {
        if (_templatesLoading.value) return
        _templatesLoading.value = true
        viewModelScope.launch {
            try {
                templatesRepository.refreshTemplates()
            } catch (t: Throwable) {
                _messages.trySend("Failed to load templates: ${t.message ?: "unknown error"}")
            } finally {
                _templatesLoading.value = false
            }
        }
    }

    /** The template body goes in at the cursor (replacing a selection); the rest of the draft stays. */
    fun applyTemplate(template: TemplateDto) {
        _composer.value = _composer.value.insertAtCursor(template.body)
        viewModelScope.launch { runCatching { templatesRepository.touchTemplate(template.template_id) } }
    }

    fun setFocusView(on: Boolean) {
        viewModelScope.launch { preferencesStore.setChatFocusView(on) }
    }

    fun toggleTemplateFavorite(template: TemplateDto) {
        viewModelScope.launch {
            try {
                templatesRepository.updateTemplate(template.template_id, isFavorite = !template.is_favorite)
            } catch (_: Throwable) {
                _messages.trySend("Failed to update template")
            }
        }
    }

    fun deleteTemplate(templateId: String) {
        viewModelScope.launch {
            try {
                templatesRepository.deleteTemplate(templateId)
            } catch (_: Throwable) {
                _messages.trySend("Failed to delete template")
            }
        }
    }

    fun saveComposerAsTemplate(name: String) {
        val body = _composer.value.text
        val trimmedName = name.trim()
        if (body.isEmpty() || trimmedName.isEmpty()) return
        viewModelScope.launch {
            try {
                templatesRepository.createTemplate(trimmedName, body)
                _messages.trySend("Saved template \"$trimmedName\"")
            } catch (t: Throwable) {
                _messages.trySend("Failed to save template: ${t.message ?: "unknown error"}")
            }
        }
    }

    fun showMessage(message: String) {
        _messages.trySend(message)
    }

    fun markScreenVisible() {
        reviewPromptCoordinator.terminalVisitStarted()
        foregroundSessionTracker.setVisible(sessionId)
        attentionNotifier.cancel(sessionId)
        screenVisible = true
        inUse.value = screenVisible && appForeground
    }

    fun markScreenHidden() {
        reportReviewVisit()
        foregroundSessionTracker.clearVisible(sessionId)
        screenVisible = false
        inUse.value = screenVisible && appForeground
    }

    fun markEndedByUser() {
        endedByUser = true
    }

    /** An error the chat screen showed the user, e.g. a failed stop. */
    fun noteReviewError() {
        reviewPromptCoordinator.noteError()
    }

    /**
     * Ends this visit for the review prompt. Read before the hide takes effect:
     * a visit ends cleanly when the user stopped the session or it is still
     * live and healthy as they leave; reconnecting, a closed session or an
     * agent error is a failure the prompt must not follow.
     */
    private fun reportReviewVisit() {
        val endedCleanly = endedByUser || _ui.value.isHealthy
        reviewPromptCoordinator.recordTerminalVisit(reviewMeter.finish(), endedCleanly)
        endedByUser = false
    }

    private val ChatUiState.isHealthy: Boolean
        get() = live && !sessionClosed && state.status != ChatStatus.ERROR

    /** Screen back on: skip the reconnect backoff a dropped socket may be sitting in. */
    fun onScreenVisible() {
        if (paused || _ui.value.live) return
        connection?.forceReconnect()
    }

    /** A short trip to another app keeps the socket; a long one closes it until the app returns. */
    private fun schedulePause() {
        pauseJob?.cancel()
        pauseJob = viewModelScope.launch {
            delay(BACKGROUND_PAUSE_MS)
            paused = true
            connection?.pause()
            onSocketDown()
        }
    }

    private fun resumeSocket() {
        pauseJob?.cancel()
        pauseJob = null
        if (!paused) return
        paused = false
        connection?.connect()
    }

    private suspend fun resolvedSession(): SessionResponse? {
        liveSessionRow()?.let { return it }
        val fetched = runCatching { sessionRepository.listSessions() }.getOrNull()
            ?.firstOrNull { it.session_id == sessionId }
        return liveSessionRow() ?: fetched
    }

    private fun liveSessionRow(): SessionResponse? =
        sessionRepository.observeSessionList().value.firstOrNull { it.session_id == sessionId }

    override fun onCleared() {
        super.onCleared()
        connection?.disconnect()
    }

    companion object {
        const val FORM_ACCEPT = "accept"
        const val FORM_DECLINE = "decline"
        const val FORM_CANCEL = "cancel"
        const val SIGN_IN_SUBMIT = "submit"
        const val SIGN_IN_CANCEL = "cancel"

        /** The [ChatUiState.answering] value while a sign-in method starts. */
        fun signInStartKey(methodId: String): String = "start:$methodId"
        private const val ALREADY_ANSWERED = "This request was already answered"
        private const val ALREADY_SENT = "This message was already sent"
        private const val PROMPT_TEXT_MAX = 100_000
        private const val IMAGES_TOTAL_MAX = 3_500_000
        private const val IMAGE_LOAD_TIMEOUT_MS = 120_000L
        private const val BACKGROUND_PAUSE_MS = 30_000L
    }
}
