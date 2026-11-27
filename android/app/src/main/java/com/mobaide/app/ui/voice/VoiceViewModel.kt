package com.mobaide.app.ui.voice

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.dto.CloudVoiceStatus
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.VoiceCapability
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import com.mobaide.app.data.network.dto.wireName
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.CloudVoiceRepository
import com.mobaide.app.data.repository.VoiceRepository
import com.mobaide.app.data.voice.AudioRecordRecorder
import com.mobaide.app.data.voice.CloudVoiceCopy
import com.mobaide.app.data.voice.DictationController
import com.mobaide.app.data.voice.DictationState
import com.mobaide.app.data.voice.InterruptReason
import com.mobaide.app.data.voice.TranscriptCleaner
import com.mobaide.app.data.voice.VoiceApiException
import com.mobaide.app.data.voice.VoiceCodes
import com.mobaide.app.data.voice.VoiceErrorCopy
import com.mobaide.app.data.voice.VoiceMicMode
import com.mobaide.app.data.voice.VoiceRoute
import com.mobaide.app.data.voice.VoiceRouting
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
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/** One install / cancel / remove in flight, or the copy of the last refusal. */
data class VoiceActionState(val busy: Boolean = false, val error: String? = null)

/** An engine choice being saved, or the copy of the last one the server did not store. */
data class VoiceEngineState(val saving: Boolean = false, val error: String? = null)

/**
 * What the mic opens while it cannot record: the [Chooser] while no engine is
 * chosen for the agent, why cloud voice input cannot record ([Cloud]), or
 * on-computer voice that is not ready ([Local]).
 */
enum class VoiceSetupContent { Chooser, Cloud, Local }

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
    /** The agent refused or missed the last cleanup change; the toggle shows its old value. */
    val cleanupError: String? = null,
    /** "Got it" was pressed on the notice shown before the first opt-in. */
    val cleanupNoticeSeen: Boolean = false,
    /** Who transcribes for the connected agent. */
    val route: VoiceRoute = VoiceRoute.Local,
    /** Cloud voice input, while it is offered for the connected agent; null otherwise. */
    val cloud: CloudVoiceStatus? = null,
    /** A fresh cloud status is on its way. */
    val cloudChecking: Boolean = false,
    /** The user's "Try again" got its answer, and cloud voice input is still unavailable. */
    val cloudRechecked: Boolean = false,
    /** The agent's own voice status could not be read. */
    val statusLoadFailed: Boolean = false,
    /** The agent did not answer whether it has voice at all. */
    val capabilityLoadFailed: Boolean = false,
    val engine: VoiceEngineState = VoiceEngineState(),
    val setupContent: VoiceSetupContent = VoiceSetupContent.Local,
) {
    /** Who transcribes, for copy like "Transcribing on …". */
    val transcriberName: String
        get() = if (route == VoiceRoute.Cloud) CloudVoiceCopy.CLOUD_NAME else agentName
}

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
 * when its source stops being the one that records (on-computer voice leaves
 * `ready`, another engine is chosen), and when the screen goes away; it is
 * stopped and transcribed when the app leaves the foreground. A cloud
 * dictation keeps its controller until it is over, whatever the cloud status
 * does meanwhile: "unavailable" arrives exactly when a recording failed, and
 * dropping the controller then would drop the audio Retry needs.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class VoiceViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val voiceRepository: VoiceRepository,
    private val cloudVoiceRepository: CloudVoiceRepository,
    agentRepository: AgentRepository,
    private val tokenStore: TokenStore,
    private val preferencesStore: PreferencesStore,
    sessionManager: SessionManager,
    appForegroundTracker: AppForegroundTracker,
    savedStateHandle: SavedStateHandle,
) : ViewModel() {
    private val sessionId: String? = savedStateHandle["sessionId"]
    private val agentKey: String? = tokenStore.getAgentId()

    /** The agent as the account holds it. */
    private val cloudAgentId: String? = agentKey

    private val capabilityState = MutableStateFlow(CapabilityProbe())
    private val controllerState = MutableStateFlow<DictationState>(DictationState.Idle)
    private val permissionFailure = MutableStateFlow<DictationState.Failed?>(null)
    private val setupVisible = MutableStateFlow(false)
    private val actionState = MutableStateFlow(VoiceActionState())
    private val sessionKind = MutableStateFlow<SessionKind?>(null)
    private val cleanupError = MutableStateFlow<String?>(null)
    private val statusLoadFailed = MutableStateFlow(false)
    private val engineState = MutableStateFlow(VoiceEngineState())

    /** A "Try again" was answered since cloud voice input last became unavailable. */
    private val cloudRechecked = MutableStateFlow(false)

    /** The chooser's Continue is being saved: the sheet keeps showing the chooser until it is. */
    private val chooserSaving = MutableStateFlow(false)
    private val _events = Channel<VoiceEvent>(Channel.BUFFERED)
    val events: Flow<VoiceEvent> = _events.receiveAsFlow()

    private var controller: DictationController? = null
    private var controllerJob: Job? = null

    /** The probe of the agent's own voice that is in flight. */
    private var localProbe: Job? = null

    /** What [controller] records for, and what a new dictation would record for now. */
    private var controllerSource: DictationSource? = null
    private var recordingSource: DictationSource? = null

    /** Only the newest engine save reports its outcome. */
    private var engineSaves = 0

    /** Bumped each time the setup sheet closes; a recheck from before it is ignored. */
    private var setupCloses = 0
    /** The session kind (wire name) the last cleanup ran for, for its failure notice. */
    private var cleanupKind: String? = null

    private val micHidden: Flow<Boolean> =
        agentKey?.let(preferencesStore::voiceMicHidden) ?: flowOf(false)

    private val cleanupPrefs: Flow<CleanupPrefs> =
        combine(preferencesStore.voiceCleanupNoticeSeen, sessionKind, cleanupError) { seen, kind, error ->
            CleanupPrefs(seen, kind, error)
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

    /** Cloud voice input while it is offered for this agent; null reads as "exactly as before it existed". */
    private val cloud: Flow<CloudVoiceStatus?> = cloudVoiceRepository.status
        .map { status -> status.takeIf { cloudAgentId != null && VoiceRouting.cloudOffered(it) } }
        .distinctUntilChanged()

    /**
     * No push says that the speech service answers again, so while it is
     * down the status is asked again by itself, like [queuedPoll] only while
     * a voice surface is on screen.
     */
    private val unavailablePoll: Flow<Unit> = cloud
        .map { it?.state == CloudVoiceStatus.STATE_UNAVAILABLE }
        .distinctUntilChanged()
        .transformLatest { unavailable ->
            while (unavailable) {
                delay(UNAVAILABLE_POLL_MS)
                runCatching { cloudVoiceRepository.refresh() }
            }
            emit(Unit)
        }
        .onStart { emit(Unit) }

    /**
     * Nothing says that an agent which did not answer is back, so while its
     * own voice could not be read it is asked again by itself, like
     * [queuedPoll] only while a voice surface is on screen.
     */
    private val unreachablePoll: Flow<Unit> =
        combine(capabilityState, voiceRepository.status, statusLoadFailed) { probe, status, failed ->
            probe.failed || (probe.capability != null && status == null && failed)
        }
            .distinctUntilChanged()
            .transformLatest { unreachable ->
                while (unreachable) {
                    delay(UNREACHABLE_POLL_MS)
                    // Joined, not run here: the first good answer ends this
                    // loop, and must not cancel the status request after it.
                    probeLocal().join()
                }
                emit(Unit)
            }
            .onStart { emit(Unit) }

    private val dictation: Flow<DictationState> =
        combine(controllerState, permissionFailure) { state, denied -> denied ?: state }

    private val localBase: Flow<LocalBase> =
        combine(capabilityState, voiceRepository.status, micHidden, agentName, statusLoadFailed) { c, s, h, n, f ->
            // A status of an agent without voice of its own is another agent's leftover.
            LocalBase(c, s.takeIf { c.capability != null }, h, n, f && s == null)
        }

    private val cloudBase: Flow<CloudBase> = combine(
        cloud,
        cloudVoiceRepository.checking,
        engineState,
        chooserSaving,
        cloudRechecked,
    ) { status, checking, engine, saving, rechecked ->
        CloudBase(status, checking, engine, saving, rechecked)
    }

    val uiState: StateFlow<VoiceUiState> = combine(
        combine(localBase, cloudBase) { local, cloud -> local to cloud },
        dictation,
        combine(setupVisible, actionState) { setup, action -> setup to action },
        cleanupPrefs,
        combine(queuedPoll, unavailablePoll, unreachablePoll) { _, _, _ -> },
    ) { (local, cloud), dictation, (setup, action), cleanup, _ ->
        val route = VoiceRouting.route(cloud.status, cloudAgentId, local.status)
        // Whether the agent's own voice is enabled is not known yet, so
        // neither is whether the mic would open the chooser: no mic meanwhile,
        // as before cloud voice input.
        val resolving = !local.capabilityKnown ||
            (local.capability != null && local.status == null && !local.statusLoadFailed)
        VoiceUiState(
            micMode = if (route == VoiceRoute.Choose && resolving) {
                VoiceMicMode.Hidden
            } else {
                VoiceRouting.micMode(
                    route = route,
                    cloud = cloud.status,
                    localMode = VoiceStatusView.micMode(local.capability, local.status, local.hidden),
                    hiddenByUser = local.hidden,
                )
            },
            capabilityKnown = local.capabilityKnown,
            capabilityLoadFailed = local.probe.failed,
            capability = local.capability,
            status = local.status,
            agentName = local.name,
            micHidden = local.hidden,
            dictation = dictation,
            setupVisible = setup,
            action = action,
            cleanupSupported = local.capability?.cleanup == true,
            // The speech service already returns clean text: cloud transcripts skip the cleanup.
            cleanupOffered = route == VoiceRoute.Local && local.capability.cleansUp(cleanup.kind),
            cleanUp = local.status?.cleanup_enabled == true,
            cleanupError = cleanup.error,
            cleanupNoticeSeen = cleanup.noticeSeen,
            route = route,
            cloud = cloud.status,
            cloudChecking = cloud.checking,
            cloudRechecked = cloud.rechecked && cloud.status?.state == CloudVoiceStatus.STATE_UNAVAILABLE,
            statusLoadFailed = local.statusLoadFailed,
            engine = cloud.engine,
            setupContent = when {
                cloud.status == null -> VoiceSetupContent.Local
                route == VoiceRoute.Choose || cloud.chooserSaving -> VoiceSetupContent.Chooser
                route == VoiceRoute.Cloud -> VoiceSetupContent.Cloud
                else -> VoiceSetupContent.Local
            },
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), VoiceUiState())

    /**
     * Who would record a new dictation, and with which limits; null while
     * nothing can record. The mic being hidden does not count: it cannot be
     * hidden while a dictation runs.
     */
    private val source: Flow<DictationSource?> =
        combine(capabilityState, voiceRepository.status, cloud) { (_, capability), status, cloud ->
            when (VoiceRouting.route(cloud, cloudAgentId, status.takeIf { capability != null })) {
                VoiceRoute.Cloud -> cloud
                    ?.takeIf { it.state == CloudVoiceStatus.STATE_READY }
                    ?.let { DictationSource(CloudVoiceStatus.ENGINE_CLOUD, it.limits.sample_rate, it.limits.max_seconds) }
                VoiceRoute.Local -> capability
                    ?.takeIf { status?.state == VoiceStatusResponse.STATE_READY }
                    ?.let { DictationSource(CloudVoiceStatus.ENGINE_LOCAL, it.sampleRate, it.maxSeconds) }
                VoiceRoute.Choose -> null
            }
        }.distinctUntilChanged()

    init {
        probeLocal()
        if (cloudAgentId != null) {
            // Best-effort seed: the connect-time refresh in UserEventBus owns
            // freshness, but this screen can mount before that socket connects.
            refreshCloudIfStale()
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
            // The next outage starts without a "still not answering".
            cloud.map { it?.state == CloudVoiceStatus.STATE_UNAVAILABLE }
                .distinctUntilChanged()
                .collect { unavailable -> if (!unavailable) cloudRechecked.value = false }
        }
        viewModelScope.launch {
            source.collect { next ->
                recordingSource = next
                releaseStaleController()
            }
        }
    }

    /** The mic, once the microphone permission is granted. */
    fun startDictation() {
        permissionFailure.value = null
        // A cloud dictation that is over (no speech, a failure) kept its
        // controller; recording again is for whoever records now.
        if (controller != null && controllerSource != recordingSource && !controllerState.value.isRunning()) {
            disposeController()
            if (recordingSource == null) {
                showSetup()
                return
            }
        }
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
        // Cloud voice input cannot record: the cached status may be a minute
        // old, so it is asked again while the sheet opens.
        val cloud = cloudVoiceRepository.status.value
        if (VoiceRouting.cloudOffered(cloud) && cloud?.state != CloudVoiceStatus.STATE_READY) refreshCloud()
    }

    fun hideSetup() {
        setupVisible.value = false
        // A "Try again" still in flight answers to a sheet that is gone.
        setupCloses += 1
        cloudRechecked.value = false
        actionState.value = VoiceActionState()
        cleanupError.value = null
        engineState.update { it.copy(error = null) }
    }

    /**
     * Ask the agent whether it has voice of its own, and for its status. One
     * at a time: a caller during a probe gets that probe.
     */
    private fun probeLocal(): Job {
        localProbe?.takeIf { it.isActive }?.let { return it }
        return viewModelScope.launch {
            val probe = runCatching { voiceRepository.capability() }
            val capability = probe.getOrNull()
            // With no agent selected there is nobody to reach: that is "no
            // voice", not an agent that did not answer.
            val failed = probe.isFailure && agentKey != null
            capabilityState.value = CapabilityProbe(known = true, capability = capability, failed = failed)
            if (capability != null) statusLoadFailed.value = runCatching { voiceRepository.refresh() }.isFailure
        }.also { localProbe = it }
    }

    fun refreshStatus() {
        if (capabilityState.value.capability == null) return
        viewModelScope.launch { statusLoadFailed.value = runCatching { voiceRepository.refresh() }.isFailure }
    }

    /**
     * "Try again" of cloud voice input that cannot record. Once the answer is
     * there, [VoiceUiState.cloudRechecked] says so: a recheck that finds the
     * speech service still down must not look like a tap that did nothing.
     */
    fun recheckCloud() {
        if (cloudAgentId == null) return
        val closes = setupCloses
        viewModelScope.launch {
            runCatching { cloudVoiceRepository.refresh() }
            if (closes != setupCloses) return@launch
            cloudRechecked.value =
                cloudVoiceRepository.status.value?.state == CloudVoiceStatus.STATE_UNAVAILABLE
        }
    }

    private fun refreshCloud() {
        if (cloudAgentId == null) return
        viewModelScope.launch { runCatching { cloudVoiceRepository.refresh() } }
    }

    /**
     * A voice surface came on screen: no push says that the speech service
     * went down or came back, so a cached status older than a minute is read
     * again. A fresh one, or one already on its way, costs no request.
     */
    fun refreshCloudIfStale() {
        if (cloudAgentId == null) return
        viewModelScope.launch { runCatching { cloudVoiceRepository.refresh(maxAgeMs = CLOUD_STALE_MS) } }
    }

    /**
     * The chooser's Continue. Mobaide Cloud closes the sheet once the choice
     * is saved. The computer also starts its download, whose costs the card
     * showed, and the sheet turns into the progress. Owned here, not by the
     * chooser: the saved choice replaces the chooser with the next step.
     */
    fun confirmEngine(engine: String) {
        if (cloudAgentId == null || engineState.value.saving) return
        chooserSaving.value = true
        saveEngine(engine, CloudVoiceCopy.CHOICE_SAVE_FAILED) { saved ->
            chooserSaving.value = false
            when {
                !saved -> Unit
                engine == CloudVoiceStatus.ENGINE_CLOUD -> setupVisible.value = false
                voiceRepository.status.value?.state == VoiceStatusResponse.STATE_NOT_INSTALLED -> install()
            }
        }
    }

    /**
     * "Use this computer instead" of cloud voice input that cannot record.
     * Nothing is downloaded yet: the sheet turns into the computer's own
     * voice, with its costs and Enable when it is not installed.
     */
    fun useLocalEngine() = saveEngine(CloudVoiceStatus.ENGINE_LOCAL, CloudVoiceCopy.CHOICE_SAVE_FAILED)

    /** "Use Mobaide Cloud instead" of on-computer voice that is not ready. */
    fun useCloudEngine() = saveEngine(CloudVoiceStatus.ENGINE_CLOUD, CloudVoiceCopy.CHOICE_SAVE_FAILED) { saved ->
        if (saved) setupVisible.value = false
    }

    /** Settings → Voice input → Speech recognition. */
    fun setEngine(engine: String) = saveEngine(engine, CloudVoiceCopy.SETTING_SAVE_FAILED)

    /**
     * Stored on the central server for every device of the account. The
     * choice shows at once and goes back, with [failure], when it is not saved.
     */
    private fun saveEngine(engine: String, failure: String, then: (saved: Boolean) -> Unit = {}) {
        val agentId = cloudAgentId ?: return
        val save = ++engineSaves
        engineState.value = VoiceEngineState(saving = true)
        viewModelScope.launch {
            val saved = try {
                cloudVoiceRepository.setEngine(agentId, engine)
                true
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                false
            }
            if (save == engineSaves) engineState.value = VoiceEngineState(error = failure.takeUnless { saved })
            then(saved)
        }
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
     * Stored on the agent, so every client of it shares the choice. Turning
     * cleanup on also records the notice as seen on this device: the UI shows
     * the notice first, and calls this only after "Got it".
     */
    fun setCleanUp(on: Boolean) {
        cleanupError.value = null
        viewModelScope.launch {
            if (on) preferencesStore.setVoiceCleanupNoticeSeen()
            try {
                voiceRepository.setCleanup(on)
            } catch (e: VoiceApiException) {
                cleanupError.value = "Couldn't save this setting on ${uiState.value.agentName}. Try again."
            }
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
        val source = recordingSource ?: return null
        val cloud = source.engine == CloudVoiceStatus.ENGINE_CLOUD
        val capability = capabilityState.value.capability
        val created = DictationController(
            api = if (cloud) cloudVoiceRepository.dictationApi() else voiceRepository.dictationApi(sessionId),
            createRecorder = { AudioRecordRecorder(context) },
            sampleRate = source.sampleRate,
            maxSeconds = source.maxSeconds,
            scope = viewModelScope,
            cleaner = {
                // The speech service already returns clean text.
                val kind = sessionKind.value?.takeIf { !cloud && capability.cleansUp(it) }?.wireName
                if (kind != null && voiceRepository.status.value?.cleanup_enabled == true) {
                    cleanupKind = kind
                    TranscriptCleaner { text -> voiceRepository.cleanup(text, kind) }
                } else {
                    null
                }
            },
        )
        controller = created
        controllerSource = source
        controllerJob = viewModelScope.launch {
            created.state.collect { state ->
                controllerState.value = state
                // The agent says voice is not ready while the cached status
                // says it is: re-read it so the mic shows the real state.
                if (state is DictationState.Failed && state.code == VoiceCodes.NOT_READY) refreshStatus()
                // The same for cloud voice input, and after each cloud
                // transcript for today's use.
                if ((state is DictationState.Failed && state.code in CLOUD_STATUS_CODES) ||
                    (cloud && state is DictationState.Result)
                ) {
                    refreshCloud()
                }
                // A cloud dictation that is over no longer holds its controller.
                if (state == DictationState.Idle) releaseStaleController()
                if (state is DictationState.Result) {
                    val notice = state.cleanupError?.let {
                        "Couldn't clean up the text, so it's inserted as heard. " +
                            VoiceErrorCopy.cleanup(it.code, uiState.value.agentName, cleanupKind)
                    }
                    _events.send(VoiceEvent.InsertTranscript(state.text, notice))
                    created.dismiss()
                }
            }
        }
        return created
    }

    /**
     * Drop a controller whose source no longer records. A cloud dictation
     * holds its controller until it is over (see the class comment).
     */
    private fun releaseStaleController() {
        if (controller == null || controllerSource == recordingSource) return
        val held = controllerSource?.engine == CloudVoiceStatus.ENGINE_CLOUD &&
            controllerState.value != DictationState.Idle
        if (!held) disposeController()
    }

    private fun disposeController() {
        // Cleared first: disposing publishes Idle, which asks to release again.
        val disposed = controller
        controller = null
        controllerSource = null
        controllerJob?.cancel()
        controllerJob = null
        disposed?.dispose()
        controllerState.value = DictationState.Idle
    }

    override fun onCleared() {
        disposeController()
    }

    private data class CleanupPrefs(val noticeSeen: Boolean, val kind: SessionKind?, val error: String?)

    /** Whether the connected agent has voice at all: not [known] yet, answered, or [failed] to answer. */
    private data class CapabilityProbe(
        val known: Boolean = false,
        val capability: VoiceCapability? = null,
        val failed: Boolean = false,
    )

    private data class LocalBase(
        val probe: CapabilityProbe,
        val status: VoiceStatusResponse?,
        val hidden: Boolean,
        val name: String,
        val statusLoadFailed: Boolean,
    ) {
        val capabilityKnown: Boolean get() = probe.known
        val capability: VoiceCapability? get() = probe.capability
    }

    private data class CloudBase(
        val status: CloudVoiceStatus?,
        val checking: Boolean,
        val engine: VoiceEngineState,
        val chooserSaving: Boolean,
        val rechecked: Boolean,
    )

    /** Who transcribes (a `CloudVoiceStatus` engine), and the limits it publishes. */
    private data class DictationSource(val engine: String, val sampleRate: Int, val maxSeconds: Int)

    private companion object {
        const val QUEUED_POLL_MS = 4_000L
        const val UNAVAILABLE_POLL_MS = 30_000L
        const val UNREACHABLE_POLL_MS = 5_000L

        /** How old the cached cloud status may be when a voice surface comes on screen. */
        const val CLOUD_STALE_MS = 60_000L

        /** The failures after which the cached cloud voice status is no longer true. */
        val CLOUD_STATUS_CODES = setOf(
            VoiceCodes.CLOUD_UNAVAILABLE,
            VoiceCodes.CLOUD_LIMIT_REACHED,
            VoiceCodes.CLOUD_DISABLED,
        )
    }
}

private fun DictationState.isRunning(): Boolean = when (this) {
    DictationState.Starting,
    is DictationState.Recording,
    is DictationState.Transcribing,
    is DictationState.Cleaning,
    -> true
    else -> false
}

/** The agent runs the CLI of the session's kind, so it cleans up only for the kinds it lists. */
private fun VoiceCapability?.cleansUp(kind: SessionKind?): Boolean =
    this != null && cleanup && kind != null && kind.wireName in cleanupKinds
