package com.mobaide.app.data.voice

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioRecordingConfiguration
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * Microphone capture with `AudioRecord`: the `VOICE_RECOGNITION` source (the
 * platform's speech-tuned input, with no noise processing that would hurt
 * recognition), mono PCM16 at the agent's rate, read in 100 ms blocks on a
 * dedicated thread.
 */
class AudioRecordRecorder(private val context: Context) : VoiceRecorder {
    private var record: AudioRecord? = null
    private var thread: Thread? = null
    private var recordingCallback: AudioManager.AudioRecordingCallback? = null
    @Volatile private var running = false
    @Volatile private var stopRequested = false

    @SuppressLint("MissingPermission")
    override suspend fun start(sampleRate: Int, sink: VoiceRecorder.Sink) = lock.withLock {
        withContext(Dispatchers.IO) {
            if (stopRequested) return@withContext
            val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
            if (!granted) throw VoiceRecorderException(VoiceCodes.MIC_PERMISSION_DENIED)
            val minBuffer = AudioRecord.getMinBufferSize(sampleRate, CHANNEL, ENCODING)
            if (minBuffer <= 0) throw VoiceRecorderException(VoiceCodes.MIC_UNAVAILABLE)
            val blockSamples = (sampleRate / 10).coerceAtLeast(160)
            val audio = try {
                AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION,
                    sampleRate,
                    CHANNEL,
                    ENCODING,
                    maxOf(minBuffer, blockSamples * 2 * 4),
                )
            } catch (e: Exception) {
                throw VoiceRecorderException(VoiceCodes.MIC_UNAVAILABLE, e.message)
            }
            if (audio.state != AudioRecord.STATE_INITIALIZED) {
                audio.release()
                throw VoiceRecorderException(VoiceCodes.MIC_UNAVAILABLE)
            }
            try {
                audio.startRecording()
            } catch (e: IllegalStateException) {
                audio.release()
                throw VoiceRecorderException(VoiceCodes.MIC_UNAVAILABLE, e.message)
            }
            // Another app holding the microphone leaves the state at stopped.
            if (audio.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                audio.release()
                throw VoiceRecorderException(VoiceCodes.MIC_UNAVAILABLE)
            }
            record = audio
            running = true
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) watchSilencing(audio, sink)
            thread = Thread({ readLoop(audio, blockSamples, sink) }, "mobaide-voice-capture").apply {
                priority = Thread.MAX_PRIORITY
                start()
            }
        }
    }

    override suspend fun stop() {
        stopRequested = true
        lock.withLock {
            withContext(Dispatchers.IO) {
                running = false
                // The loop finishes the block it is reading and delivers it
                // before exiting: at most ~100 ms.
                thread?.join(JOIN_TIMEOUT_MS)
                thread = null
                unwatchSilencing()
                record?.let { audio ->
                    runCatching { audio.stop() }
                    audio.release()
                }
                record = null
            }
        }
    }

    private fun readLoop(audio: AudioRecord, blockSamples: Int, sink: VoiceRecorder.Sink) {
        val block = ShortArray(blockSamples)
        while (running) {
            val read = audio.read(block, 0, block.size, AudioRecord.READ_BLOCKING)
            when {
                read > 0 -> {
                    sink.level(Pcm16.meterLevel(block, 0, read))
                    sink.samples(block, read)
                }
                read < 0 -> {
                    // ERROR_DEAD_OBJECT and friends: the input went away.
                    if (running) sink.interrupted(InterruptReason.Device)
                    return
                }
            }
        }
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private fun watchSilencing(audio: AudioRecord, sink: VoiceRecorder.Sink) {
        val manager = context.getSystemService(AudioManager::class.java) ?: return
        val sessionId = audio.audioSessionId
        val callback = object : AudioManager.AudioRecordingCallback() {
            override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>) {
                val mine = configs.firstOrNull { it.clientAudioSessionId == sessionId } ?: return
                if (mine.isClientSilenced && running) sink.interrupted(InterruptReason.Call)
            }
        }
        manager.registerAudioRecordingCallback(callback, Handler(Looper.getMainLooper()))
        recordingCallback = callback
    }

    private fun unwatchSilencing() {
        val callback = recordingCallback ?: return
        recordingCallback = null
        context.getSystemService(AudioManager::class.java)?.unregisterAudioRecordingCallback(callback)
    }

    private companion object {
        /**
         * Shared by every instance: a new recording's start waits for the
         * previous one's stop, so a quick cancel-then-record never opens a
         * second capture while the first still holds the microphone.
         */
        val lock = Mutex()
        const val CHANNEL = AudioFormat.CHANNEL_IN_MONO
        const val ENCODING = AudioFormat.ENCODING_PCM_16BIT
        const val JOIN_TIMEOUT_MS = 500L
    }
}
