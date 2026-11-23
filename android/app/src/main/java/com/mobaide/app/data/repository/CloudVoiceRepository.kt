package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.auth.requireAccessToken
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.CloudVoiceStatus
import com.mobaide.app.data.network.dto.VoiceDictationAudioRequest
import com.mobaide.app.data.voice.VoiceApi
import com.mobaide.app.data.voice.VoiceDictationOpened
import com.mobaide.app.data.voice.VoiceFinishResult
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * An engine choice shows at once: [status] is the last server snapshot with
 * the choices still being saved laid over it, so neither a refresh that
 * started earlier nor a push from another device shows the old choice
 * meanwhile. Every write is skipped once the account has changed.
 */
class CloudVoiceRepository(
    private val centralApi: CentralApi,
    private val userIdentityStore: UserIdentityStore,
) {
    // Saves outlive the sheet that started them, so closing it right after a
    // choice still stores the choice.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val lock = Any()
    private val saveMutex = Mutex()
    private var server: CloudVoiceStatus? = null
    private val pendingEngines = mutableMapOf<String, String>()

    /** Refreshes started so far, and the newest one whose answer may still be applied. */
    private var refreshesStarted = 0L
    private var refreshFloor = 0L
    private var answeredAt: Long? = null

    private val _status = MutableStateFlow<CloudVoiceStatus?>(null)
    val status: StateFlow<CloudVoiceStatus?> = _status.asStateFlow()

    private val refreshing = MutableStateFlow(0)

    /** A fresh status is on its way. */
    val checking: Flow<Boolean> = refreshing.map { it > 0 }.distinctUntilChanged()

    /**
     * Read the status again. With [maxAgeMs], nothing is asked while the
     * last answer is younger than that or a fresh one is already on its way.
     * A failure keeps the last answer and reaches the caller.
     */
    suspend fun refresh(maxAgeMs: Long = 0) {
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.getUserAccessToken()
        val ticket = synchronized(lock) {
            if (token != null && maxAgeMs > 0) {
                val age = answeredAt?.let { (System.nanoTime() - it) / 1_000_000 }
                if ((age != null && age < maxAgeMs) || refreshing.value > 0) return
            }
            ++refreshesStarted
        }
        if (token == null) {
            applyAnswer(owner, ticket, CloudVoiceStatus.NOT_OFFERED)
            return
        }
        refreshing.update { it + 1 }
        try {
            val answer = try {
                centralApi.getCloudVoice(token)
            } catch (e: ApiException) {
                // A server from before cloud voice input has no such route.
                if (e.code != 404) throw e
                CloudVoiceStatus.NOT_OFFERED
            }
            applyAnswer(owner, ticket, answer)
        } finally {
            refreshing.update { it - 1 }
        }
    }

    /**
     * Choose where [agentId]'s dictations are transcribed, for every device
     * of the account. Saves run one at a
     * time, in call order, so a later choice is never overtaken by an earlier
     * one. When the server does not store it, the choice goes back and the
     * [com.mobaide.app.data.voice.VoiceApiException] reaches the caller.
     */
    suspend fun setEngine(agentId: String, engine: String) {
        val owner = userIdentityStore.getUserId()
        synchronized(lock) {
            pendingEngines[agentId] = engine
            publishLocked()
        }
        // UNDISPATCHED: the body runs here up to `withLock`, so saves queue at
        // the mutex in call order rather than in Dispatchers.Default's order.
        scope.async(start = CoroutineStart.UNDISPATCHED) {
            try {
                val saved = saveMutex.withLock {
                    // A save queued before a sign-out must not run with the next
                    // account's token.
                    if (userIdentityStore.getUserId() != owner) return@withLock null
                    voiceCall {
                        centralApi.putCloudVoiceEngine(userIdentityStore.requireAccessToken(), agentId, engine)
                    }
                }
                if (saved != null) {
                    // The answer is newer than every refresh that started before it.
                    val ticket = synchronized(lock) { ++refreshesStarted }
                    applyAnswer(owner, ticket, saved)
                }
            } finally {
                synchronized(lock) {
                    if (pendingEngines[agentId] == engine) {
                        pendingEngines.remove(agentId)
                        publishLocked()
                    }
                }
            }
        }.await()
    }

    /** The cloud dictation routes, for the uploader. */
    fun dictationApi(): VoiceApi = object : VoiceApi {
        override suspend fun create(): VoiceDictationOpened = voiceCall {
            val body = centralApi.createCloudDictation(userIdentityStore.requireAccessToken())
            VoiceDictationOpened(body.dictation_id, body.sample_rate, body.max_seconds, body.max_chunk_bytes)
        }

        override suspend fun audio(dictationId: String, seq: Int, pcm16B64: String) {
            voiceCall {
                centralApi.appendCloudAudio(
                    userIdentityStore.requireAccessToken(),
                    dictationId,
                    VoiceDictationAudioRequest(seq, pcm16B64),
                )
            }
        }

        override suspend fun finish(dictationId: String, lastSeq: Int): VoiceFinishResult = voiceCall {
            val body = centralApi.finishCloudDictation(userIdentityStore.requireAccessToken(), dictationId, lastSeq)
            VoiceFinishResult(body.state, body.text)
        }

        override fun discard(dictationId: String) {
            scope.launch {
                runCatching {
                    centralApi.deleteCloudDictation(userIdentityStore.requireAccessToken(), dictationId)
                }
            }
        }
    }

    /** The account boundary: nothing of the previous account stays, and its answers in flight are dropped. */
    fun clear() {
        synchronized(lock) {
            server = null
            pendingEngines.clear()
            answeredAt = null
            refreshFloor = refreshesStarted
            publishLocked()
        }
    }

    /** An answer older than one already applied, or for another account, is dropped. */
    private fun applyAnswer(owner: String?, ticket: Long, answer: CloudVoiceStatus) {
        synchronized(lock) {
            if (ticket <= refreshFloor || userIdentityStore.getUserId() != owner) return
            refreshFloor = ticket
            server = answer
            answeredAt = System.nanoTime()
            publishLocked()
        }
    }

    private fun publishLocked() {
        val snapshot = server
        _status.value = when {
            snapshot == null || pendingEngines.isEmpty() -> snapshot
            else -> snapshot.copy(engines = snapshot.engines + pendingEngines)
        }
    }
}
