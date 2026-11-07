package com.mobaide.app.ui.voice

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.VoiceCapability
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.VoiceRepository
import com.mobaide.app.data.voice.AudioRecordRecorder
import com.mobaide.app.data.voice.DictationController
import com.mobaide.app.data.voice.DictationState
import com.mobaide.app.data.voice.InterruptReason
import com.mobaide.app.data.voice.TranscriptCleaner
import com.mobaide.app.data.voice.VoiceApiException
import com.mobaide.app.data.voice.VoiceCodes
import com.mobaide.app.data.voice.VoiceErrorCopy
import com.mobaide.app.data.voice.VoiceMicMode
import com.mobaide.app.data.voice.VoiceStatusView
import com.mobaide.app.notifications.AppForegroundTracker
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.transformLatest
import kotlinx.coroutines.launch
import javax.inject.Inject

/** One install / cancel / remove in flight, or the copy of the last refusal. */
data class VoiceActionState(val busy: Boolean = false, val error: String? = null)

data class VoiceUiState(
    val micMode: VoiceMicMode = VoiceMicMode.Hidden,
    /** False until the connected agent answered whether it has voice at all. */
    val capabilityKnown: Boolean = false,
    val capability: VoiceCapability? = null,
    val status: VoiceStatusResponse? = null,
    val agentName: String = DEFAULT_AGENT_NAME,
    val micHidden: Boolean = false,
    val dictation: DictationState = DictationState.Idle,
    val setupVisible: Boolean = false,
    val action: VoiceActionState = VoiceActionState(),
    /** The agent can clean up transcripts (`capabilities.voice.cleanup`). */
    val cleanupSupported: Boolean = false,
    /** ...and this session's AI agent can do it: the setup sheet offers the toggle. */
    val cleanupOffered: Boolean = false,
    val cleanUp: Boolean = false,
    /** "Got it" was pressed on the notice shown before the first opt-in. */
    val cleanupNoticeSeen: Boolean = false,
)

sealed interface VoiceEvent {
    /**
     * Put the transcript at the composer's cursor; never send it. [notice]
     * says why it was not cleaned up, for a snackbar.
     */
    data class InsertTranscript(val text: String, val notice: String? = null) : VoiceEvent
}

const val DEFAULT_AGENT_NAME = "your computer"

/**
 * A dictation is cancelled when the agent stops being connected (a switch),
 * when voice leaves `ready`, and when the screen goes away; it is stopped and
 * transcribed when the app leaves the foreground.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class VoiceViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val voiceRepository: VoiceRepository,
    agentRepository: AgentRepository,
    private val tokenStore: TokenStore,
    private val preferencesStore: PreferencesStore,
    sessionManager: SessionManager,
    appForegroundTracker: AppForegroundTracker,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {
    private val sessionId: String? = savedStateHandle["sessionId"]
    private val agentKey: String? = tokenStore.getAgentId() ?: tokenStore.getAgentUrl()

    private val capabilityState = MutableStateFlow<Pair<Boolean, VoiceCapability?>>(false to null)
    private val controllerState = MutableStateFlow<DictationState>(DictationState.Idle)
    private val permissionFailure = MutableStateFlow<DictationState.Failed?>(null)
    private val setupVisible = MutableStateFlow(false)
    private val actionState = MutableStateFlow(VoiceActionState())
    private val sessionKind = MutableStateFlow<SessionKind?>(null)
    private val _events = Channel<VoiceEvent>(Channel.BUFFERED)
    val events: Flow<VoiceEvent> = _events.receiveAsFlow()

    private var controller: DictationController? = null
    private var controllerJob: Job? = null

    private val micHidden: Flow<Boolean> =
        agentKey?.let(preferencesStore::voiceMicHidden) ?: flowOf(false)

    /** Eager: the controller reads it synchronously when a transcript arrives. */
    private val cleanUp: StateFlow<Boolean> =
        (agentKey?.let(preferencesStore::voiceCleanup) ?: flowOf(false))
            .stateIn(viewModelScope, SharingStarted.Eagerly, false)

    private val cleanupPrefs: Flow<CleanupPrefs> =
        combine(cleanUp, preferencesStore.voiceCleanupNoticeSeen, sessionKind) { on, seen, kind ->
            CleanupPrefs(on, seen, kind)
        }

    private val agentName: Flow<String> = agentRepository.observeAgents().map { agents ->
        val id = tokenStore.getAgentId()
        agents.firstOrNull { it.agent_id == id }?.name ?: DEFAULT_AGENT_NAME
    }

    /**
     * While another process holds the install (`queued`), agents older than
     * the agent's lock watcher publish nothing, so the status is polled — only
     * while someone collects [uiState], i.e. while a voice surface is on screen.
     */
    private val queuedPoll: Flow<Unit> = voiceRepository.status
        .map { it?.state == VoiceStatusResponse.STATE_QUEUED }
        .distinctUntilChanged()
        .transformLatest { queued ->
            while (queued) {
                delay(QUEUED_POLL_MS)
                runCatching { voiceRepository.refresh() }
            }
            emit(Unit)
        }
        .onStart { emit(Unit) }

    private val dictation: Flow<DictationState> =
        combine(controllerState, permissionFailure) { state, denied -> denied ?: state }

    val uiState: StateFlow<VoiceUiState> = combine(
        combine(capabilityState, voiceRepository.status, micHidden, agentName) { c, s, h, n ->
            Quad(c, s, h, n)
        },
        dictation,
        combine(setupVisible, actionState) { setup, action -> setup to action },
        cleanupPrefs,
        queuedPoll,
    ) { base, dictation, (setup, action), cleanup, _ ->
        val (capability, status, hidden, name) = base
        val cleanupSupported = capability.second?.cleanup == true
        VoiceUiState(
            micMode = VoiceStatusView.micMode(capability.second, status, hidden),
            capabilityKnown = capability.first,
            capability = capability.second,
            status = status,
            agentName = name,
            micHidden = hidden,
            dictation = dictation,
            setupVisible = setup,
            action = action,
            cleanupSupported = cleanupSupported,
            cleanupOffered = cleanupSupported && cleanup.kind.cleansUp(),
            cleanUp = cleanup.on,
            cleanupNoticeSeen = cleanup.noticeSeen,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), VoiceUiState())

    init {
        viewModelScope.launch {
            val capability = runCatching { voiceRepository.capability() }.getOrNull()
            capabilityState.value = true to capability
            if (capability != null) runCatching { voiceRepository.refresh() }
        }
        viewModelScope.launch {
            appForegroundTracker.isForeground.collect { foreground ->
                if (!foreground) controller?.stop(InterruptReason.Background)
            }
        }
        viewModelScope.launch {
            sessionManager.authState.collect { state ->
                if (state != SessionManager.AuthState.FullyConnected) disposeController()
            }
        }
        viewModelScope.launch {
            voiceRepository.status
                .map { it?.state == VoiceStatusResponse.STATE_READY }
                .distinctUntilChanged()
                .collect { ready -> if (!ready) disposeController() }
        }
    }

    /** The mic, once the microphone permission is granted. */
    fun startDictation() {
        permissionFailure.value = null
        val current = controller ?: createController() ?: return
        current.toggle()
    }

    fun stopDictation() {
        controller?.stop()
    }

    fun cancelDictation() {
        permissionFailure.value = null
        controller?.cancel()
    }

    fun retryDictation() {
        controller?.retry()
    }

    fun dismissDictation() {
        permissionFailure.value = null
        controller?.dismiss()
    }

    fun onMicPermissionDenied() {
        permissionFailure.value = DictationState.Failed(
            code = VoiceCodes.MIC_PERMISSION_DENIED,
            retryable = false,
            message = null,
            hasAudio = false,
        )
    }

    fun showSetup() {
        setupVisible.value = true
        refreshStatus()
    }

    fun hideSetup() {
        setupVisible.value = false
        actionState.value = VoiceActionState()
    }

    fun refreshStatus() {
        viewModelScope.launch { runCatching { voiceRepository.refresh() } }
    }

    fun install() = runAction { voiceRepository.install() }
    fun cancelInstall() = runAction { voiceRepository.cancelInstall() }
    fun remove() = runAction { voiceRepository.remove() }
    fun reinstall() = runAction { voiceRepository.reinstall() }

    fun setMicHidden(hidden: Boolean) {
        val key = agentKey ?: return
        viewModelScope.launch { preferencesStore.setVoiceMicHidden(key, hidden) }
        if (hidden) setupVisible.value = false
    }

    /** The terminal's session; null on Settings, where no transcript is cleaned up. */
    fun setSessionKind(kind: SessionKind?) {
        sessionKind.value = kind
    }

    /**
     * Turning cleanup on also records the notice as seen: the UI shows the
     * notice first, and calls this only after "Got it".
     */
    fun setCleanUp(on: Boolean) {
        val key = agentKey ?: return
        viewModelScope.launch {
            if (on) preferencesStore.setVoiceCleanupNoticeSeen()
            preferencesStore.setVoiceCleanup(key, on)
        }
    }

    private fun runAction(action: suspend () -> Unit) {
        if (actionState.value.busy) return
        actionState.value = VoiceActionState(busy = true)
        viewModelScope.launch {
            val error = try {
                action()
                null
            } catch (e: VoiceApiException) {
                if (e.network) "Couldn't reach ${uiState.value.agentName}." else VoiceErrorCopy.action(e.code)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                VoiceErrorCopy.action(null)
            }
            actionState.value = VoiceActionState(busy = false, error = error)
        }
    }

    private fun createController(): DictationController? {
        val capability = capabilityState.value.second ?: return null
        val created = DictationController(
            api = voiceRepository.dictationApi(sessionId),
            createRecorder = { AudioRecordRecorder(context) },
            sampleRate = capability.sampleRate,
            maxSeconds = capability.maxSeconds,
            scope = viewModelScope,
            cleaner = {
                if (capability.cleanup && sessionKind.value.cleansUp() && cleanUp.value) {
                    TranscriptCleaner { text -> voiceRepository.cleanup(text) }
                } else {
                    null
                }
            },
        )
        controller = created
        controllerJob = viewModelScope.launch {
            created.state.collect { state ->
                controllerState.value = state
                // The agent says voice is not ready while the cached status
                // says it is: re-read it so the mic shows the real state.
                if (state is DictationState.Failed && state.code == VoiceCodes.NOT_READY) refreshStatus()
                if (state is DictationState.Result) {
                    val notice = state.cleanupError?.let {
                        "Couldn't clean up the text, so it's inserted as heard. " +
                            VoiceErrorCopy.cleanup(it.code, uiState.value.agentName)
                    }
                    _events.send(VoiceEvent.InsertTranscript(state.text, notice))
                    created.dismiss()
                }
            }
        }
        return created
    }

    private fun disposeController() {
        controller?.dispose()
        controller = null
        controllerJob?.cancel()
        controllerJob = null
        controllerState.value = DictationState.Idle
    }

    override fun onCleared() {
        disposeController()
    }

    private data class CleanupPrefs(val on: Boolean, val noticeSeen: Boolean, val kind: SessionKind?)

    private data class Quad(
        val capability: Pair<Boolean, VoiceCapability?>,
        val status: VoiceStatusResponse?,
        val hidden: Boolean,
        val name: String,
    )

    private companion object {
        const val QUEUED_POLL_MS = 4_000L
    }
}

/** Only Claude Code can clean up a transcript so far: the agent runs its CLI. */
private fun SessionKind?.cleansUp(): Boolean = this == SessionKind.Claude
