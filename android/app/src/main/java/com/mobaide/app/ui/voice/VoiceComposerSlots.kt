package com.mobaide.app.ui.voice

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.voice.DictationState
import com.mobaide.app.data.voice.VoiceMicMode

/** What a composer shows for voice input: the mic, and the bar that replaces its buttons while dictating. */
class VoiceComposerSlots(
    val micButton: (@Composable () -> Unit)?,
    val dictationBar: (@Composable () -> Unit)?,
)

/**
 * The voice wiring every composer shares: transcripts go to [onTranscript], a
 * tick marks start and stop, the screen stays on while dictating, and leaving
 * the screen cancels a dictation (a configuration change does not). Also hosts
 * the setup sheet.
 */
@Composable
fun rememberVoiceComposerSlots(
    voiceViewModel: VoiceViewModel,
    onTranscript: (String) -> Unit,
): VoiceComposerSlots {
    val voiceState by voiceViewModel.uiState.collectAsStateWithLifecycle()
    val latestOnTranscript by rememberUpdatedState(onTranscript)
    LaunchedEffect(voiceViewModel) {
        voiceViewModel.events.collect { event ->
            when (event) {
                is VoiceEvent.InsertTranscript -> latestOnTranscript(event.text)
            }
        }
    }

    val haptics = LocalHapticFeedback.current
    val recording = voiceState.dictation is DictationState.Recording
    var wasRecording by remember { mutableStateOf(false) }
    LaunchedEffect(recording) {
        if (recording != wasRecording) {
            haptics.performHapticFeedback(HapticFeedbackType.LongPress)
            wasRecording = recording
        }
    }
    val view = LocalView.current
    val dictating = when (voiceState.dictation) {
        DictationState.Starting, is DictationState.Recording, is DictationState.Transcribing -> true
        else -> false
    }
    DisposableEffect(view, dictating) {
        view.keepScreenOn = dictating
        onDispose { view.keepScreenOn = false }
    }
    val activity = LocalContext.current.findActivity()
    DisposableEffect(voiceViewModel) {
        onDispose {
            if (activity?.isChangingConfigurations != true) voiceViewModel.cancelDictation()
        }
    }
    val requestMic = rememberMicPermissionRequest(
        onGranted = voiceViewModel::startDictation,
        onDenied = voiceViewModel::onMicPermissionDenied,
    )

    if (voiceState.setupVisible) {
        VoiceSetupSheet(
            state = voiceState,
            onDismiss = voiceViewModel::hideSetup,
            onEnable = voiceViewModel::install,
            onCancelInstall = voiceViewModel::cancelInstall,
            onRetry = voiceViewModel::install,
            onHideMic = { voiceViewModel.setMicHidden(true) },
        )
    }

    val micButton: (@Composable () -> Unit)? =
        if (voiceState.micMode == VoiceMicMode.Hidden) {
            null
        } else {
            {
                VoiceMicButton(
                    mode = voiceState.micMode,
                    status = voiceState.status,
                    onClick = {
                        if (voiceState.micMode == VoiceMicMode.Ready) requestMic() else voiceViewModel.showSetup()
                    },
                )
            }
        }
    val dictationBar: (@Composable () -> Unit)? =
        when (voiceState.dictation) {
            DictationState.Idle, is DictationState.Result -> null
            else -> {
                {
                    VoiceDictationBar(
                        state = voiceState.dictation,
                        agentName = voiceState.agentName,
                        onStop = voiceViewModel::stopDictation,
                        onCancel = voiceViewModel::cancelDictation,
                        onRetry = voiceViewModel::retryDictation,
                        onDismiss = voiceViewModel::dismissDictation,
                        onRecordAgain = requestMic,
                    )
                }
            }
        }
    return VoiceComposerSlots(micButton = micButton, dictationBar = dictationBar)
}

/**
 * [transcript] at this value's cursor (replacing a selection), with a space on
 * either side where it would otherwise run into a word, the cursor after it.
 * Null for a blank transcript. Never sends: the user reviews the text first.
 */
fun TextFieldValue.withTranscript(transcript: String): TextFieldValue? {
    val clean = transcript.trim()
    if (clean.isEmpty()) return null
    val start = selection.min.coerceIn(0, text.length)
    val end = selection.max.coerceIn(start, text.length)
    val before = text.substring(0, start)
    val after = text.substring(end)
    val lead = if (before.isNotEmpty() && !before.last().isWhitespace()) " " else ""
    val trail = if (after.isNotEmpty() && !after.first().isWhitespace()) " " else ""
    val inserted = lead + clean + trail
    return TextFieldValue(
        text = before + inserted + after,
        selection = TextRange(before.length + inserted.length),
    )
}
