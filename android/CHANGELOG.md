# Android Changelog

## [Unreleased]

### Added
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
