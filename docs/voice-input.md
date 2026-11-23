# Voice input (dictation)

Voice input lets a user dictate into a session. The app or browser records the microphone, and one of two engines turns the audio into text. The user chooses the engine for each agent:

- **Mobaide Cloud** (recommended, preselected in the chooser). The client uploads the recording to the central server, which has a hosted speech service transcribe it. Nothing is installed on the computer. See [Cloud voice input](#cloud-voice-input).
- **On this computer.** The client streams the audio to the user's own agent: through the relay, or directly when a phone uses direct-connection mode. The agent transcribes it on that computer with NVIDIA Parakeet TDT 0.6B v3.

The text lands in the composer (mobile) or a review field (web).

**Nothing is ever sent automatically**: the user reviews the text and sends it themselves.

It is opt-in per agent. Nothing is recorded or downloaded until the user chooses an engine. For on-computer voice, the app first shows the real download, disk and memory cost the agent reports, plus a warning on low-spec computers.

Most of this document describes on-computer voice. The dictation rules (audio format, buffering, retries, interruptions, insertion) are the same for both engines.

Removing the agent with **Also uninstall Mobaide from this computer** stops voice and deletes the model and runtime too (docs/deployment.md, *Uninstalling the agent*).

References:

- Agent half: GH #736 (`mobaide_agent/mobaide/voice/`).
- Clients: GH #737 (Android, iOS, web).
- Wire contract: PROTOCOL.md, proto-v7 delta "2026-10-14, on-PC voice input". It covers `capabilities.voice`, `/api/voice/*`, the `voice.changed` event and the error codes. Cloud voice input is the delta "2026-11-22, cloud voice input".
- Public explainer: `mobaide.com/voice-input` (`central_server/templates/marketing_voice_input.html`, docs/website.md *The voice input page*). It restates the costs, limits and setup steps below, so update it with them.

## Cloud voice input

The central server offers it when its configuration names a speech service (docs/deployment.md, *Cloud voice input*). Without that, `GET /api/voice/cloud` answers `off` and every client behaves as before.

### Which engine an agent uses

Clients read `GET /api/voice/cloud` (state, limits, today's use, the choice per agent) and resolve each agent the same way (web `voiceRoute` in `voice/voiceEngine.ts`):

| Case | Engine |
|---|---|
| Cloud is not offered (`off`, an unknown state, an older server) | On this computer, as before |
| The user chose for this agent | That choice |
| No choice, and the agent's own voice is already enabled | On this computer |
| No choice otherwise | The first mic tap opens the chooser |

The choice is stored on the central server (`PUT /api/voice/cloud/engines/{agent_id}`) and the `cloud_voice.changed` user event brings it to the user's other devices. Because Mobaide Cloud needs nothing from the agent, the mic also shows for an agent without `capabilities.voice`.

### What the user sees

- **Chooser** (first mic tap). Two radio cards. **Mobaide Cloud** is first, marked *Recommended* and preselected. **On this computer** shows the agent's real costs, or the reason it cannot be chosen. **Continue** saves the choice. With the computer chosen, it also starts the download. Nothing is recorded before the user confirms.
- **Mic.** With Mobaide Cloud, the mic records when the state is `ready`. For `unavailable` and `limit_reached` it shows the warning glyph, and a tap opens a sheet that says why. The sheet has **Try again** and, when the agent has voice of its own, **Use this computer instead**.
- **Settings.** The agent's Voice input settings start with **Speech recognition**: Mobaide Cloud or On this computer.
  - Before a choice, nothing is selected and one line says "Choose one to turn on voice input for {agent}." The on-computer install panel is not shown there.
  - With Mobaide Cloud they show the per-recording limit, and today's use against the daily limit only when one is set. The on-computer controls stay available while the model is installed there.
  - A disabled **On this computer** says why under the option: the agent is still being checked, did not answer, or cannot run voice input.
  - When the agent did not answer, the section says "Couldn’t reach {agent}. Is it online?" and asks again every 5 s while a voice surface is shown, so it recovers when the agent returns. On web it also asks again at once when the agent list shows the agent online.
- **Failures.** `cloud_unavailable` keeps the recording and offers **Retry**. The server checks the speech service when a dictation opens, so an outage stops the recording after a few words. `cloud_limit_reached` and `cloud_disabled` say so and do not offer Retry. A recording the speech service does not answer in time still counts toward today's use; a refusal or a failed connection does not.
- **Held until it is over.** A cloud dictation keeps its controller until it ends, also when the status turns `unavailable` meanwhile. The failure and its buffered audio then stay on screen for Retry. On web, a review that waits for more dictation keeps its text the same way.
- **No cleanup.** The speech service already returns clean text, so cloud transcripts skip the transcript cleanup below, and its toggle is hidden while Mobaide Cloud is selected.

### Server side

`central_server/central_server/cloud_voice/`:

| File | Role |
|---|---|
| `router.py`, `models.py`, `errors.py` | The routes, bodies and refusals of PROTOCOL.md |
| `service.py` | Dictations in memory (one per user, 64 in all), the daily limits, the health check of the speech service, the engine choices |
| `speech_client.py` | The HTTP client of the speech service (an OpenAI-compatible chat API with inline audio) and the transcription instruction |
| `audio.py` | Chunk decoding, the silence check, WAV wrapping, and the cleanup of the returned text (control characters, runaway repetition) |

- **Languages.** 28, and 12 more in beta, detected as the user speaks: Bulgarian, Croatian, Czech, Danish, Dutch, English, Estonian, Finnish, French, German, Hindi, Hungarian, Italian, Japanese, Korean, Latvian, Lithuanian, Polish, Portuguese, Romanian, Russian, Slovak, Slovenian, Spanish, Swedish, Thai, Turkish, Ukrainian. 12 more are in beta (the text can need more corrections): Arabic, Bengali, Chinese (Mandarin), Greek, Hebrew, Indonesian, Maltese, Marathi, Tagalog, Tamil, Telugu, Vietnamese. The lists belong to the speech service, not to this repo: change it, the website (`#languages`, the landing card) and `README.md` together when the service changes. On-computer voice has its own 25 (`catalog.py`).
- **Limits.** 2 minutes per recording. No daily limit by default: the per-account and all-accounts daily caps are optional settings (`0` = no limit). With no per-account cap, `usage.limit_seconds` is `0` and the apps show no allowance. The counts are in SQLite (`cloud_voice_usage`), so a deploy does not reset them. At most 4 requests go to the speech service at once.
- **Health.** A failed request marks the speech service as down. While it is down, the server asks it again at most every 5 s, and only when a client asks for the status, opens a dictation or retries one.
- **Privacy.** The central server holds the audio in memory until the speech service answers, and stores neither audio nor text. It sends the account's internal id with each request, so the operator of the speech service can find and block an account that abuses it. `central_server/legal/privacy.md` §3.9 says what the speech service keeps.

### Client files

| Piece | Android (`android/app/src/main/java/com/mobaide/app/`) | iOS (`ios/Mobaide/`) | Web (`web/src/`) |
|---|---|---|---|
| Status, engine choice, cloud dictation routes | `data/repository/CloudVoiceRepository.kt` | `Data/Repository/CloudVoiceRepository.swift` | `api/cloudVoiceRepository.ts`, `voice/useCloudVoice.ts` |
| Engine resolution, mic mode | `data/voice/VoiceEngine.kt` | `Data/Voice/VoiceRoute.swift` | `voice/voiceEngine.ts` |
| Chooser, cloud panel, settings selector | `ui/voice/CloudVoiceComponents.kt` | `UI/Voice/CloudVoiceViews.swift` | `voice/VoiceEngineChooser.tsx`, `voice/CloudVoicePanel.tsx`, `voice/VoiceEngineSetting.tsx` |
| Shared copy | `data/voice/CloudVoiceCopy.kt` | `Data/Voice/CloudVoiceCopy.swift` | `voice/cloudVoiceCopy.ts` |

The uploader, buffer, controller and recorder are shared with on-computer voice: only the API behind them and the source of the limits differ.

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

While `queued`, clients also poll the status every 4 s, because agents older than the lock watcher publish nothing for another process's install.

Voice settings live in two places:

- **Mobile:** **Settings → Voice input** (the connected agent).
- **Web:** each online agent's card (**Voice input**).

Both offer enable, cancel or stop, retry, remove, reinstall, the mic toggle, the model and its license (`model.notice`, CC BY 4.0).

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
  - **Web:**
    - An editable review field, then **Insert**, which uses the Templates paste path (a sanitized bracketed paste, never Enter). Claude Code may display long text as `[Pasted text]`.
    - The review's mic button records again. The new transcript goes in at the field's cursor (replacing a selection), a space apart from the words around it. A cancel, an empty result or a failure returns to the review, with the cursor and scroll position as they were.
    - Its **Insert dictated text directly** checkbox skips the review for that agent from then on (stored per agent in the browser, `skipReviewAgents` in `mobaide-voice-prefs`). The transcript is pasted the same way as soon as it arrives, and only a paste the session refuses still opens the review. The agent's Voice input settings turn the review back on.
    - Once the session has ended, the toolbar mic and **Record again** are disabled, because a transcript can only be pasted into a running session (an unknown status, such as while connecting, keeps them enabled).

## Transcript cleanup

An opt-in, per-agent setting, off by default: **Clean up dictated text with the session's AI agent: remove filler words, fix punctuation and technical terms**. Before the text reaches the user, the session's own AI agent tidies each transcript with its cheapest model (OpenCode, GitHub Copilot and Pi: their default model; Cursor: Auto; Junie: Junie Lite): fillers, stutters and false starts go, self-corrections keep the final version ("twenty, no wait, thirty" → "thirty"), and punctuation and technical terms are fixed ("package dot json" → `package.json`). The speaker's language and wording are kept. It works in Claude Code (Claude Haiku), Codex (the newest GPT Luna), Antigravity (Gemini Flash on low thinking), OpenCode (its default model), GitHub Copilot (its default model), Cursor (Auto), Junie (Junie Lite) and Pi (its default model) sessions; the copy is kept agent-neutral so other session kinds can join.

- **Where it is offered.**
  - Only when the agent advertises `capabilities.voice.cleanup`.
  - Settings: mobile **Settings → Voice input** and the web agent card's **Voice input**, for any agent that supports it.
  - In a session (terminal or chat), only where it works: a session whose `kind` is in `capabilities.voice.cleanup_kinds` (`claude`, `codex`, `antigravity`, `opencode`, `copilot`, `cursor`, `junie`, `pi`; an agent that advertises `cleanup` without the list means `claude` only). There the session's CLI is known to be installed. The setup sheet (mobile) or dialog (web) offers the toggle once voice is downloading or ready, so the first dictation can already be cleaned up, and the web review offers it too.
  - Storage: on the agent, one choice per agent shared by every client (web, Android, iOS). The clients read `cleanup_enabled` from the voice status and change it with `PUT /api/voice/cleanup/settings`; the agent keeps it in `<voice root>/cleanup.json` and announces each change with `voice.changed`. A change shows at once and goes back, with a "Couldn't save this setting" message, when the agent does not store it.
- **The notice.** The first time the toggle is turned on in a browser or app install, a notice says where the text goes: the session's AI agent on the computer, its cheapest model (Claude Haiku for Claude Code, GPT Luna for Codex, Gemini Flash for Antigravity, Junie Lite for Junie; OpenCode, GitHub Copilot, Cursor and Pi use their default model), the user's own account and usage, nothing stored by Mobaide. The setting changes only after **Got it**; **Cancel** leaves it off. The acknowledgement is stored once per device (`cleanupNoticeSeen` in `mobaide-voice-prefs`; `voice_cleanup_notice_seen` on Android and iOS). Shared copy: web `voice/CleanupToggle.tsx`, Android `VoiceCleanupCopy` (`ui/voice/VoiceComponents.kt`), iOS `VoiceCleanupCopy` (`UI/Voice/VoiceViews.swift`).
- **When the setting is read.** When a transcript arrives, so a change made while recording applies to that transcript. On the web, turning it on from the review also cleans up the text already in the review (the field is read-only meanwhile), unless that text was cleaned up already.
- **Agent side.** `POST /api/voice/cleanup {text, kind}` (`mobaide_agent/mobaide/voice/cleanup.py`, `TranscriptCleaner`) runs one hidden run of the session kind's CLI. All eight share the rules:
  - One prompt (`CLEANUP_PROMPT`): clean the transcript, never answer it; plain text, no Markdown. The transcript is wrapped in `<transcript>` tags and goes on stdin, never in argv.
  - The same binary resolution as that CLI's sessions (`MOBAIDE_CLAUDE_BIN` / `MOBAIDE_CODEX_BIN` / `MOBAIDE_AGY_BIN` / `MOBAIDE_OPENCODE_BIN` / `MOBAIDE_COPILOT_BIN` / `MOBAIDE_CURSOR_BIN` / `MOBAIDE_JUNIE_BIN` / `MOBAIDE_PI_BIN`, PATH, fallbacks).
  - The run's cwd is `<state prefix>/voice-cleanup`, which holds only the prompt and settings files (none for OpenCode, GitHub Copilot, Junie and Pi; for Cursor, `.cursor/cli.json`). Cursor's config folder, `<state prefix>/voice-cleanup-cursor`, is a sibling of it.
  - Only the binary path may be quoted in argv: an npm `.cmd` shim runs through `cmd /c`, which strips the first and last quote of a line holding more than two. That is why flags use `--tools=` rather than `--tools ""`, and name their files relative to the cwd.
  - No tools: the model can only answer with text (Junie keeps only `submit`, the tool that ends its run and carries the answer).
  - A cancelled request still holds its slot until the CLI exits, and the CLI is killed.
  - Typical latency is 2–5 s. The call has a 20 s budget and runs at most two at once, whatever the CLI.
  - The text is never logged; the log names the kind, the model and the sizes.

  | Kind | Command | Model | Notes |
  |---|---|---|---|
  | `claude` | `claude -p --model haiku --effort low --safe-mode --no-session-persistence --tools= --system-prompt-file cleanup-prompt.md --output-format json` | Claude Haiku (`MAX_THINKING_TOKENS=0`) | `--safe-mode` skips CLAUDE.md, hooks, MCP servers, plugins and skills but keeps the login (`--bare` would drop OAuth subscriptions). Needs Claude Code ≥ 2.1.169. |
  | `codex` | `codex exec --model <luna> -c model_reasoning_effort=low -c model_instructions_file=cleanup-prompt.md -c features.shell_tool=false -c features.unified_exec=false -c features.hooks=false -c web_search=disabled --sandbox read-only --skip-git-repo-check --ephemeral --ignore-user-config --ignore-rules --json -` | The first Luna in the cached `codex debug models` catalog (newest first), else `gpt-6-luna` | The prompt file replaces Codex's coding-agent instructions. `codex login status` runs first, inside the same 20 s budget, because a signed-out `codex exec` retries for longer than the budget. Only a run that exits 0 after `turn.completed` counts. `--ignore-user-config` skips MCP servers, notify scripts and profiles; auth still comes from `CODEX_HOME`. |
  | `antigravity` | `agy --agent mobaide-voice-cleanup --model <flash-low> --input-format stream-json --output-format stream-json --disable-slash-commands --settings antigravity-settings.json` | The first Gemini Flash on low thinking in the cached `agy models` list, else `gemini-3.8-flash-low` | `agy` has no system-prompt option, so the prompt is the body of a project custom agent, `.agents/agents/mobaide-voice-cleanup.md` in the work directory. Its `tools: []` and `excludeDefaultComponents: true` drop agy's coding-agent prompt and built-in tools. Under the default agent, Gemini Flash sometimes took a chatty transcript as a message to itself and ran `ls`. `agy` reads a plain prompt only from argv, so the transcript goes in one stream-json user message on stdin. The settings file sets `toolPermission: strict` as a backstop, so headless `agy` denies any tool. `agy` treats an empty answer as an error and asks again without end, so the prompt says to return fillers-only text unchanged. Antigravity keeps the run in its own local conversation history; no flag turns that off. |
  | `opencode` | `opencode run --standalone --agent mobaide-voice-cleanup --title Voice-cleanup --archived --format json` | OpenCode's default model (OpenCode names no cheapest one), one request per cleanup | The transcript goes on stdin. `PWD` is removed from the env, and `OPENCODE_DISABLE_PROJECT_CONFIG=1` and `OPENCODE_DISABLE_CLAUDE_CODE=1` are set (OpenCode 2.3.1+ otherwise loads `~/.claude/CLAUDE.md` when the user has no global `AGENTS.md`). The cleanup agent goes inline in `OPENCODE_CONFIG_CONTENT`, merged into the PC's own value: `system` = the shared prompt, deny-all `permissions`, a top-level deny-all rule last, `share: "disabled"`. No file is written to the work dir. The output is JSONL: an `error` event fails the cleanup even at exit code 0 (its message is shown, for example "Free usage limit reached…"), and any tool event discards the run. OpenCode 1.x has no `--standalone`, and OpenCode 2.2.0–2.2.5 have no `--archived`: both give `cleanup_outdated` ("run `opencode upgrade`"). An `OPENCODE_CONFIG_CONTENT` that is not a plain JSON object: `cleanup_failed`. Limits: the user's global OpenCode config still applies (its `AGENTS.md` joins the prompt, its MCP servers start on each cleanup and add up to about 3 s). `--archived` keeps each run (title `Voice-cleanup`, folder = the `voice-cleanup` work dir) out of OpenCode's session picker and `--continue` (`opencode session list` still prints it); the chat's Continue list also hides that folder. The title has no space on purpose: a second quoted token breaks `cmd /c` for an npm shim in a spaced Windows path. |
  | `copilot` | `copilot --output-format json --stream off --available-tools=mobaide-no-tools --deny-tool=shell --deny-tool=write --deny-tool=read --deny-tool=url --deny-tool=memory --no-custom-instructions --disable-builtin-mcps --sync-session=off` | Copilot's default model (Copilot names no cheapest one and has no command that lists models), one request per cleanup | `copilot` has no system-prompt option, and it puts a custom agent's prompt below its own coding-agent prompt, where a small model ignores it. So the rules go before the transcript in the one message on stdin (piped stdin is the prompt of a non-interactive run). `--available-tools` is the list of tools the model may see; a name that matches no tool leaves it none, and any tool event discards the run. The deny rules are a second barrier (they win over every allow rule), and the run's environment drops `COPILOT_ALLOW_ALL`, `COPILOT_ASSISTED_APPROVAL`, `COPILOT_PROMPT_TURN_DELIMITER` and the `GITHUB_COPILOT_PROMPT_MODE_*` switches (`REPO_HOOKS`, `WORKSPACE_MCP`, `EXTENSIONS`). Copilot turns `@<path>` in a prompt into a file attachment when the path exists (its own input handling, not a tool; no option turns it off), so a transcript with an `@` mention is not sent: `cleanup_failed`, and the text stays as heard. An e-mail address is not a mention. Mobaide writes no project configuration to the work folder. `--sync-session=off` keeps the run out of the account's synced history. The output is JSONL: the last `assistant.message` is the answer, and only a `result` event with exit code 0 counts; a `session.error` message is shown on a failure. A signed-out Copilot fails with "run `copilot login`"; a Copilot that does not know a flag gives `cleanup_outdated` ("run `copilot update`"). Limits: Copilot's own coding-agent prompt stays in the request (about 5,000 input tokens per cleanup), the user's personal hooks and MCP servers still load, and Copilot keeps each run in its local session history (folder = the `voice-cleanup` work dir); no flag turns that off. The chat's Continue list hides that folder. Measured with Copilot CLI 1.0.109: 5 to 7 s per cleanup. |
  | `cursor` | `cursor-agent --print --output-format stream-json --mode ask --trust --model auto` | Cursor's Auto model (included in the Free plan) | The rules and the transcript go on stdin. The work directory holds `.cursor/cli.json`, which denies every tool, so the model can only answer with text. The text goes to Cursor (Anysphere) under the user's account. `CURSOR_CONFIG_DIR=<state prefix>/voice-cleanup-cursor` keeps the run off the user's Cursor settings: Cursor saves a run's model as the user's default, so `--model auto` alone would overwrite it. Before each run the agent copies the user's `cli-config.json` (from `CURSOR_CONFIG_DIR`, else `$XDG_CONFIG_HOME/cursor`, else `~/.cursor`) into that folder, so network and privacy settings still apply; the user's file is only read, and a missing or broken file keeps the last copy. The sign-in is not in that folder, so the run uses the user's account. The run's chat goes to `voice-cleanup-cursor/chats`, not the user's `~/.cursor/chats`, and each later Cursor cleanup removes run chats older than 60 s. Cursor still writes its per-project record for the work folder under `~/.cursor/projects/`. |
  | `junie` | `junie --output-format json --model junie-lite --allowed-tools=submit --skip-update-check --config-default-locations=false --mcp-default-locations=false --skill-default-locations=false --agent-default-location=false --command-default-location=false --extensions-default-location=junie-extensions` | Junie Lite (`junie-lite`), JetBrains' free model and the only model of a Lite-only account | Junie's one-shot mode is a coding agent, and no option replaces its system prompt. So the rules and the transcript both go on stdin, as the task (piped stdin is the task of a non-interactive run). With the transcript alone as the task, Junie Lite tried to carry out coding orders. `--allowed-tools` leaves only `submit`, the tool that ends a run (Junie always keeps it), so the model can run nothing; without the flag, a one-shot run executes `bash` with no approval. A run must end with a `submit` call, whose description asks for a Markdown report of the changes, so Junie's copy of the prompt replaces the "Output only the cleaned text" line with a rule that the call's `solution_summary` is the cleaned text alone, and to submit the same text again if Junie refuses the call. The default-location flags keep the user's `config.json` (it can hold hooks), MCP servers, skills, custom agents, commands and extensions off; `junie-extensions` is a folder that does not exist. The environment drops the `JUNIE_*` variables that would add config, hooks, MCP servers, skills, agents, commands or models, move the run to another folder or session, or change its task, prompt, input, output, effort or provider. `JAVA_TOOL_OPTIONS` gets `-Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8` appended (Junie's JVM writes piped output in the system code page: ANSI on Windows, ASCII under a C locale, which turned Cyrillic into `?`); the JVM's "Picked up JAVA_TOOL_OPTIONS" line on stderr is never shown. The output is one JSON object: `result` is the answer; `errors` fails the cleanup, also at exit code 0 (signed out: "Cannot find authorization" → "run `junie` there and sign in"). A non-empty `changes` list discards the run, and so does an answer that reports on the cleanup: Markdown headings, list items, bold or the word "transcript" when the dictated text has none. Junie's stderr banner names its log file and is never shown. `no such option` gives `cleanup_outdated` ("run `junie update`"). Limits: for Junie Lite, Junie refuses once per run a `submit` that changed no file when it judges the task to need code changes, and the model's next answer then often turns into a report; so dictated orders to create documentation or config files (a README, `.gitignore`, a license file, a contributing guide) fail after 7–13 s and keep the text as heard; orders to create code files mostly clean. The user can turn cleanup off for the agent. The user's global `~/.junie/AGENTS.md` still joins the prompt, and Junie keeps each run in its session history (folder = the `voice-cleanup` work dir) and writes a log file per run; no option turns these off. The chat's Continue list hides that folder. Measured with Junie 26.11.1: about 4 s per cleanup; 11 of 11 mixed test transcripts clean; in an end-to-end run, 20 of 29 cleanups were clean in 4–8 s and 9 failed safely (all orders to create documentation or config files); no report reached the review as cleaned text. |
  | `pi` | `pi --mode json --no-session --no-tools --no-extensions --no-skills --no-prompt-templates --no-context-files --no-mcp --no-themes --no-approve --offline --thinking off --system-prompt cleanup-prompt.md` | Pi's default model, one request per cleanup | The transcript goes on stdin. `--no-session` keeps the run out of Pi's session history, and the other flags leave Pi no tools, extensions, skills or context files. The output limit is 4 MiB. |
- **Client flow (same controller state on all three: `cleaning`).**
  - The cleanup is part of transcribing to the user: the web dialog keeps its **Transcribing** title and the mobile dictation bar stays in place; only the status line changes to "Cleaning up the text…". **Cancel** throws the dictation away, as it does while transcribing. There is no separate cleanup window and no skip button.
  - Clients give up after 35 s (above the agent's 20 s budget and the relay's 30 s wait).
  - Web: the review then says "Cleaned up by the session's AI agent." and offers **Show original**.
  - Mobile: the cleaned text goes into the composer like any transcript; the composer is the review, so there is no **Show original**.
  - Any failure (the session's CLI missing, too old or not signed in, a timeout, an answer that is not a cleanup) keeps the transcript as heard and says why, naming that CLI ("Codex on laptop is too old. Run \"codex update\" there."): in the web review (with **Insert dictated text directly** on, a failed cleanup still opens the review), or in a snackbar (Android) or toast (iOS) after the text is inserted.
- **Cost and privacy.** The transcript goes to the AI agent's provider under the user's own login — Anthropic for Claude Code, OpenAI for Codex, Google for Antigravity, the user's configured model provider for OpenCode, GitHub for GitHub Copilot, Cursor (Anysphere) for Cursor, JetBrains for Junie, the model provider Pi uses for Pi: the subscription's usage limits or AI credits, or the API key's billing (fractions of a cent per dictation). Mobaide never stores or logs it; in relay mode the request crosses the central server in memory only, like the rest of the dictation traffic. The notice says so.

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
- **Privacy policy** (`central_server/legal/privacy.md` §3.4, §3.7, §3.9, §4): for on-computer voice, audio is used only while dictating, sent over TLS to the user's own agent (relayed in memory, or directly in mobile direct-connection mode), never stored or logged. §3.9 covers Mobaide Cloud: the audio is not stored, and the speech service keeps the recognised text for up to 14 days to stop abuse. The opt-in transcript cleanup above is the one path where the text leaves the computer, through the session's own AI agent (Claude Code, Codex, Antigravity, OpenCode, GitHub Copilot, Cursor, Junie or Pi); §3.4 and §6 say so. The public `/voice-input` page says so too (*Where your voice goes* and the FAQ).

## Release checklist (manual steps)

1. Deploy **central_server first**.
   - It carries the relay's bodyless-204 fix from PR #740, and older servers drop keep-alive after a relayed 204.
   - Deploying it also ships the privacy policy, and reloads Caddy (`caddy reload --force`) for `microphone=(self)`.
2. Re-answer **Play Data Safety** (Audio → Voice or sound recordings: collected, processed ephemerally, not shared, optional, app functionality) and publish the **App Store App Privacy** Audio Data entry.
   - Both are recorded in docs/app-conversion-measurement.md.
   - Submit them with the first client release that has the mic.
3. For cloud voice input:
   - Set `CLOUD_VOICE_URL`, `CLOUD_VOICE_API_KEY` and `CLOUD_VOICE_MODEL` in the central server's `.env` (docs/deployment.md, *Cloud voice input*). Until then every client keeps on-computer voice only.
   - The deploy ships the privacy policy (§3.9) and the `/voice-input` page, which both describe Mobaide Cloud. Do not turn the feature on before they are live.
   - Re-check the store answers of step 2: the recording is still processed ephemerally, but the speech service keeps the recognised text for up to 14 days.
   - Local checks use `tests/e2e/manual_stack.py up --fake-cloud-voice`.
4. Run one real-model test on a real Android phone, an iPhone and a desktop browser.
   - Local checks use `tests/e2e/manual_stack.py up --fake-voice`.
   - Its fake engine answers `fake transcript <ms> ms` and needs voiced audio (RMS above about -44 dBFS, or about 10 dB above the room's noise floor).
   - Silence and steady background noise end as "No speech was recognised".
