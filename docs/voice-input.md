# Voice input (dictation)

Voice input lets a user dictate into a session. The app or browser records the microphone and streams the audio to the user's own agent: through the relay, or directly when a phone uses direct-connection mode. The agent transcribes it on that computer with NVIDIA Parakeet TDT 0.6B v3 and returns text, which lands in the composer (mobile) or a review field (web). **Nothing is ever sent automatically**: the user reviews the text and sends it themselves.

It is opt-in per agent. Nothing is downloaded until the user enables voice from the app; enabling shows the real download, disk and memory cost the agent reports, plus a warning on low-spec computers. Removing the agent with **Also uninstall Mobaide from this computer** stops voice and deletes the model and runtime too (docs/deployment.md, *Uninstalling the agent*).

- Agent half: GH #736 (`mobaide_agent/mobaide/voice/`).
- Clients: GH #737 (Android, iOS, web).
- Wire contract: PROTOCOL.md, proto-v7 delta "2026-10-14, on-PC voice input". It covers `capabilities.voice`, `/api/voice/*`, the `voice.changed` event and the error codes.
- Public explainer: `mobaide.com/voice-input` (`central_server/templates/marketing_voice_input.html`, docs/website.md *The voice input page*). It restates the costs, limits and setup steps below, so update it with them.

## Client module (same shape on all three)

| Piece | Android (`android/app/src/main/java/com/mobaide/app/`) | iOS (`ios/Mobaide/`) | Web (`web/src/`) |
|---|---|---|---|
| Audio format: chunk size, Float32→PCM16, LE, base64 | `data/voice/Pcm16.kt` | `Data/Voice/Pcm16.swift` | `voice/pcm16.ts` |
| Buffer: holds every sample until the transcript arrives | `data/voice/PcmBuffer.kt` | `Data/Voice/PcmBuffer.swift` | `voice/pcmBuffer.ts` |
| Uploader: single request pump | `data/voice/DictationUploader.kt` | `Data/Voice/DictationUploader.swift` | `voice/dictationUploader.ts` |
| Controller: UI state machine | `data/voice/DictationController.kt` | `Data/Voice/DictationController.swift` | `voice/dictationController.ts` |
| Recorder | `AudioRecordRecorder.kt` (`VOICE_RECOGNITION`, 16 kHz) | `AudioEngineRecorder.swift` (`AVAudioEngine` + `AVAudioConverter`) | `voice/recorder.ts` + `public/voice/capture-worklet.js` + `voice/resampler.ts` |
| Status, actions, dictation routes | `data/repository/VoiceRepository.kt` | `Data/Repository/VoiceRepository.swift` | `api/voiceRepository.ts`, `voice/useVoiceStatus.ts` |
| Error copy, status → UI | `VoiceErrorCopy.kt`, `VoiceStatusView.kt` | `VoiceErrorCopy.swift`, `VoiceStatusView.swift` | `voiceErrorCopy.ts`, `voiceStatusView.ts` |
| UI | `ui/voice/` (mic, dictation bar, setup sheet, `VoicePanel`, Settings section) | `UI/Voice/` (same) | `routes/terminal/voice/`, `voice/VoicePanel.tsx`, `routes/agents/VoiceSettingsDialog.tsx` |

`tests/protocol_fixtures/voice/pcm16_chunks.json` pins chunking, byte order, base64 (no line breaks; Android uses `java.util.Base64`, never `android.util.Base64.DEFAULT`) and the Float32→PCM16 rule for all three clients and the agent's decoder.

## How the mic looks

Shown only when the agent advertises `capabilities.voice`. The status comes from `GET /api/voice/status` and `voice.changed`, and an unknown state reads as unavailable.

| Status | Mic | Tap |
|---|---|---|
| `not_installed` | off style | enable sheet: real costs from `model.download_bytes` / `disk_bytes` / `ram_bytes`, languages, low-spec warning from `host.reasons`, **Enable** / **Not now** / **Hide the mic for this agent** |
| `queued` / `downloading` / `verifying` | progress | progress and **Cancel**; while `repairing` the label reads "Updating voice (≈N MB)…" and the button **Stop** keeps the model |
| `failed` | warning | reason (copy by `error.code`, raw message behind Details) and **Retry** |
| `ready` | mic | dictate |
| `unsupported`, unknown, capability absent, or hidden by the user | hidden | — |

While `queued`, clients also poll the status every 4 s, because agents older than the lock watcher publish nothing for another process's install. Voice settings live in mobile **Settings → Voice input** (the connected agent), and on the web on each online agent's card (**Voice input**). Both offer enable, cancel or stop, retry, remove, reinstall, the mic toggle, the model and its license (`model.notice`, CC BY 4.0).

## Dictation rules

- **Audio.** 16 kHz mono PCM16, sent in ~1 s chunks (`min(rate*2, max_chunk_bytes)`) while the user speaks. Exactly one audio request is in flight per dictation. `finish` follows only after the last chunk is acknowledged, and reports the highest seq the agent **accepted**.
- **Buffered for retry.** Every sample stays in memory until the transcript arrives (at most `max_seconds`, 120 s: 3.84 MB), so Retry never needs the user to speak again.
- **Network trouble.** Requests retry with backoff (0.5/1/2/4 s), and recording continues meanwhile. After the user stops, 30 s without progress fails the dictation as retryable.
- **Recovery.**
  - `seq_gap` resumes at `expected_seq` (at most 3 resyncs).
  - `dictation_not_found` re-creates the dictation and re-uploads from seq 0 (at most 2 restarts).
  - `too_long` stops capture and finishes.
  - `transcription_failed` is latched by the agent: it is never retried automatically, and Retry opens a new dictation. A countdown shows the last 15 s.
- **Cleanup.** Cancel sends a fire-and-forget DELETE whose errors are ignored, because older relays reset the connection after the 204. An id that a cancelled create returns is deleted too.
- **Interruptions.** Backgrounding the app, hiding the tab, a phone call, an audio route change or an unplugged mic all **stop and transcribe** what was captured. Leaving the session or switching agents **cancels**.
- **Insertion.**
  - **Mobile:** the transcript goes at the composer's cursor, with a space added where needed, and the composer is focused. On iOS 17 it is appended, because SwiftUI exposes the selection only from iOS 18.
  - **Web:** an editable review field, then **Insert**, which uses the Templates paste path (a sanitized bracketed paste, never Enter). Claude Code may display long text as `[Pasted text]`. The review's mic button records again; the new transcript goes in at the field's cursor (replacing a selection), a space apart from the words around it, and a cancel, an empty result or a failure returns to the review. Its **Insert dictated text directly** checkbox skips the review for that agent from then on (stored per agent in the browser, `skipReviewAgents` in `mobaide-voice-prefs`): the transcript is pasted the same way as soon as it arrives, and only a paste the session refuses still opens the review. The agent's Voice input settings turn the review back on. Once the session has ended, the toolbar mic and **Record again** are disabled, because a transcript can only be pasted into a running session (an unknown status, such as while connecting, keeps them enabled).

## Permissions, privacy and stores

- **Android:** `RECORD_AUDIO`, asked on the first mic tap. A rationale is shown if the user declined before; when permanently denied, the app links to its settings. Recording happens only in the foreground.
- **iOS:**
  - `NSMicrophoneUsageDescription` is set, and `AVAudioApplication` asks on the first recording.
  - When denied, the app links to Settings.
  - `PrivacyInfo.xcprivacy` declares Audio Data (App Functionality, not tracking).
- **Web:**
  - The browser prompts the first time; when denied, the app shows per-browser instructions.
  - Only the SPA origin allows `microphone=(self)` (both Caddyfiles); central_server pages keep `microphone=()` (docs/deployment.md).
  - The AudioWorklet is a static same-origin file, because the CSP is `script-src 'self'` with no `blob:`.
- **Privacy policy** (`central_server/legal/privacy.md` §3.4, §3.7, §4): audio is used only while dictating, sent over TLS to the user's own agent (relayed in memory, or directly in mobile direct-connection mode), never stored or logged.

## Release checklist (manual steps)

1. Deploy **central_server first**. It carries the relay's bodyless-204 fix from PR #740, and older servers drop keep-alive after a relayed 204. Deploying it also ships the privacy policy, and reloads Caddy (`caddy reload --force`) for `microphone=(self)`.
2. Re-answer **Play Data Safety** (Audio → Voice or sound recordings: collected, processed ephemerally, not shared, optional, app functionality) and publish the **App Store App Privacy** Audio Data entry. Both are recorded in docs/app-conversion-measurement.md. Submit them with the first client release that has the mic.
3. Run one real-model test on a real Android phone, an iPhone and a desktop browser. Local checks use `tests/e2e/manual_stack.py up --fake-voice`; its fake engine answers `fake transcript <ms> ms` and needs voiced audio (RMS above about -44 dBFS, or about 10 dB above the room's noise floor); silence and steady background noise end as "No speech was recognised".
