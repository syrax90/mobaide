# Android Changelog

## [Unreleased]

### Added
- Prompt-templates management screen (reachable from Settings → "Prompt
  templates"): lists all saved templates with rename, delete, and
  favorite-toggle actions backed by ``TemplatesRepository``.
- In-terminal templates picker bottom-sheet: recents / favorites / all
  sections with live search. Tapping a template inserts it into the input bar;
  long-press surfaces a context menu for quick delete or favorite-toggle.
- Save-as-template dialog: saves the current ``InputBar`` text as a new named
  template via a single API call.
- ``inputText`` hoisted from local ``remember`` state in ``TerminalScreen``
  into ``TerminalViewModel`` (``StateFlow<TextFieldValue>``) so the templates
  picker, the save-template action, ``InputBar``, and ``FullScreenInputSheet``
  all share a single source of truth.
- ``template.created``, ``template.updated``, and ``template.deleted`` push
  events handled in ``UserEventBus``; forwarded to
  ``TemplatesRepository.applyUpserted`` / ``applyDeleted`` for optimistic
  local updates without a full refresh.
- ``BuildConfig.APP_PROTOCOL_VERSION`` and ``BuildConfig.MIN_SERVER_PROTOCOL_VERSION``
  expose the wire-contract version so future ``server_info`` / ``/api/version``
  comparisons stay in lockstep with the Python side.
- ``VersionGateRepository`` probes ``GET /api/version`` before any WS connect
  and emits ``ServerCompatibility.{Compatible,Incompatible,Unknown}``. An
  ``Incompatible`` outcome flips ``SessionManager.AuthState`` into the new
  ``IncompatibleServer`` terminal state, which collapses the navigation graph
  onto the dedicated upgrade screen with a Play Store deep link.
- ``InAppUpdateManager`` wraps ``com.google.android.play:app-update-ktx`` and
  triggers the flexible flow on every ``MainActivity.onResume``. The immediate
  flow is reserved for the IncompatibleServer recovery path.
- WebSocket close code ``4002`` (PROTOCOL_INCOMPATIBLE) routes through the new
  ``onIncompatibleServer`` callback and never retries.
- Forward-compat ``AgentEvent.UnknownEnvelopeType`` + ``AgentEvent.AgentOutdated``
  surface unknown envelope types and agent-version drift without breaking the
  active connection.
- Release-signing config block in ``android/app/build.gradle.kts`` reads from
  env vars (CI) or ``signing.properties`` (local) and falls back to debug
  signing when neither is provided.
- Double-tap on any ``❯ ``-prefixed terminal row (or its wrapped continuation
  rows) copies the command text into the bottom InputBar and focuses it,
  without submitting. Single-tap (submit-on-tap) behaviour is unchanged.

### Removed
- QR-based device-pairing flow (``QrScannerScreen``, ``ManualPairingScreen``,
  ``PairingViewModel``, ``PairingRepository``, ``PairingDto``) deleted. The
  feature had no reachable UI entry point and called a server endpoint
  (``POST /api/pairing/complete``) that does not exist. Removing it drops the
  ``CAMERA`` permission from ``AndroidManifest.xml`` (eliminating the Play
  Store privacy-policy obligation and the install-time camera permission
  prompt) and removes five CameraX/MLKit dependencies from the APK.
