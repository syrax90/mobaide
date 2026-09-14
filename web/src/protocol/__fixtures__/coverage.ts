/**
 * Which golden fixtures the TypeScript arm asserts, and who owns the rest.
 *
 * iOS gates its fixture suite in both directions: every fixture file must map to
 * a registered type, and every registered type must have a fixture
 * (`MobaideTests/Fixtures/DTOFixtureTests.swift`). The web client cannot claim
 * all 80 DTOs at WEB-04 — most of them describe surfaces (git, projects,
 * auto-scan, session create) that later milestones build — so the gate is
 * expressed as an explicit split instead of a silent subset.
 *
 * `fixtureCoverage.test.ts` asserts that COVERED ∪ DEFERRED is exactly the set
 * of files on disk. A new fixture file therefore fails the TypeScript suite
 * until someone decides which side it belongs on, which is the same drift
 * protection the iOS registry provides.
 */

const OWNER = {
  /**
   * The agent auto-updater's force-install response. Android drives it from the
   * agent-outdated banner (`SessionListScreen` → `AgentUpdateRepository`); iOS
   * defers it to CLA-120 and web has no banner at all yet. Mis-filed under the
   * auto-scan milestone before WEB-10 looked at it.
   */
  agentUpdate: 'not built on web — agent-outdated banner (Android shipped, iOS CLA-120)',
  /**
   * Shipped surfaces whose shapes come from the committed `openapi-typescript`
   * output. A hand-written Zod mirror would be a second source of truth for a
   * contract the generated types and the schema-drift CI gate already pin.
   *
   * Applies on both axes: the agent's Codex-login pair lands here for the same
   * reason the central-server auth DTOs do — one required field each, no
   * default, no three-state, nothing Zod would add.
   *
   * WEB-08 moved the whole pairing/per-agent-token group here rather than
   * mirroring it. Every one of those DTOs is REST-only: the pairing payload
   * that *does* need a hand-written parser is the `MOBA1|…` QR string, which
   * appears in no OpenAPI schema and is asserted from `pairing/qr_payload.json`
   * below. `agent.status_changed`, the one pairing-adjacent WebSocket payload,
   * already has its Zod mirror in `dto/userEvents.ts`.
   *
   * WEB-09 moved the project-write, project-order, git and template-write DTOs
   * here on the same test. They are request and response bodies only: the shapes
   * that also travel over a socket (`ProjectInfo`) or that no schema documents
   * at all (the `busy_by` 409) are the ones that got Zod mirrors instead.
   *
   * WEB-10 moved the Auto Scan request bodies and the Linear filter-picker
   * responses here on the same test, and kept Zod mirrors only for
   * `AutoScanInstanceDto` and `AutoScanListResponse` — the two that also arrive
   * over the agent channel and the `ws/autoscan` snapshot.
   */
  generated: 'shipped — typed by the generated OpenAPI schema',
  /** Editing a custom agent type on web: CLA-231. */
  customTypes: 'CLA-231 — custom agent type editing on web',
  /**
   * Saved shortcuts were removed from the web client — a physical keyboard
   * makes the chord chips redundant — so no web code parses these shapes
   * anymore. The fixture files stay: Android and iOS still assert them.
   */
  shortcutsRemoved: 'saved shortcuts removed from web; Android/iOS still consume',
  /** Web has no APNs equivalent; Web Push is not implemented. */
  notApplicable: 'not applicable on web',
  /**
   * No client reads the endpoint this pins. Android has the repository call
   * (`SessionRepository.getScrollback`) with no UI caller, iOS never added one,
   * and the web terminal keeps its 10,000-row scrollback inside xterm — a REST
   * round trip would be a second, staler copy of what is already on screen.
   */
  unusedByClients: 'no client consumes this endpoint',
} as const;

/** Fixture files this milestone's tests read and assert. */
export const COVERED_FIXTURES: readonly string[] = [
  'ansi/ansi_sgr.json',
  'frames/terminal_frames.json',
  'frames/autoscan_frames.json',
  'keys/key_chords.json',
  'pairing/qr_payload.json',
  'relay/relay_mapping.json',
  'dto/agent/AntigravityOptionsModel.json',
  'dto/agent/AutoScanFilterDto.json',
  'dto/agent/AutoScanInstanceDto.json',
  'dto/agent/AutoScanListResponse.json',
  'dto/agent/BusyByResponse.json',
  'dto/agent/BusyHolder.json',
  'dto/agent/ClaudeOptionsModel.json',
  'dto/agent/CodexOptionsModel.json',
  'dto/agent/CreateSessionRequest.json',
  'dto/agent/ProjectInfo.json',
  'dto/agent/SessionKind.json',
  'dto/agent/SessionResponse.json',
  'dto/agent/VersionInfoResponse.json',
  'dto/central/CreateCustomAgentTypeRequest.json',
  'dto/central/CustomAgentTypeDto.json',
  'dto/central/CustomTypeDeletedPayload.json',
  'dto/central/FirstAgentPairedPayload.json',
  'dto/central/TemplateDeletedPayload.json',
  'dto/central/TemplateDto.json',
];

/** Fixture files no TypeScript test reads yet, each with the milestone that will. */
export const DEFERRED_FIXTURES: Readonly<Record<string, string>> = {
  'dto/agent/CodexLoginRequest.json': OWNER.generated,
  'dto/agent/CodexLoginResponse.json': OWNER.generated,
  'dto/agent/ForceUpdateResponse.json': OWNER.agentUpdate,
  'dto/agent/GitDeleteItem.json': OWNER.generated,
  'dto/agent/GitDeleteRequest.json': OWNER.generated,
  'dto/agent/GitDeleteResponse.json': OWNER.generated,
  'dto/agent/GitDeleteResultItem.json': OWNER.generated,
  'dto/agent/GitDiffResponse.json': OWNER.generated,
  'dto/agent/GitItem.json': OWNER.generated,
  'dto/agent/GitItemsResponse.json': OWNER.generated,
  'dto/agent/GitStatusResponse.json': OWNER.generated,
  'dto/agent/LinearLabelDto.json': OWNER.generated,
  'dto/agent/LinearStateDto.json': OWNER.generated,
  'dto/agent/ProjectOrderRequest.json': OWNER.generated,
  'dto/agent/ProjectUpdate.json': OWNER.generated,
  'dto/agent/ProjectWrite.json': OWNER.generated,
  'dto/agent/RefreshRequest.json': OWNER.generated,
  'dto/agent/RunNowRequest.json': OWNER.generated,
  'dto/agent/RunNowResponse.json': OWNER.generated,
  'dto/agent/ScrollbackResponse.json': OWNER.unusedByClients,
  'dto/agent/StartScanRequest.json': OWNER.generated,
  'dto/agent/StopScanRequest.json': OWNER.generated,
  'dto/agent/TokenRequest.json': OWNER.generated,
  'dto/agent/TokenResponse.json': OWNER.generated,

  'dto/central/AgentSummaryResponse.json': OWNER.generated,
  'dto/central/CancelAccountDeletionResponse.json': OWNER.generated,
  'dto/central/CreateAgentRequest.json': OWNER.generated,
  'dto/central/CreateAgentResponse.json': OWNER.generated,
  'dto/central/CreateShortcutRequest.json': OWNER.shortcutsRemoved,
  'dto/central/CreateTemplateRequest.json': OWNER.generated,
  'dto/central/DeleteAccountReauthDetail.json': OWNER.generated,
  'dto/central/DeleteAccountReauthErrorBody.json': OWNER.generated,
  'dto/central/DeleteAccountRequest.json': OWNER.generated,
  'dto/central/DeviceCodeConfirmRequest.json': OWNER.generated,
  'dto/central/DeviceCodeMintResponse.json': OWNER.generated,
  'dto/central/DeviceCodeStatusResponse.json': OWNER.generated,
  'dto/central/DeviceResponse.json': OWNER.generated,
  'dto/central/ForgotPasswordRequest.json': OWNER.generated,
  'dto/central/ForgotPasswordResponse.json': OWNER.generated,
  'dto/central/GoogleAuthRequest.json': OWNER.generated,
  'dto/central/MeResponse.json': OWNER.generated,
  'dto/central/MobileTokenRequest.json': OWNER.generated,
  'dto/central/MobileTokenResponse.json': OWNER.generated,
  'dto/central/PairingClaimRequest.json': OWNER.generated,
  'dto/central/PairingClaimResponse.json': OWNER.generated,
  'dto/central/ReRegisterAgentResponse.json': OWNER.generated,
  'dto/central/RegisterPushTokenRequest.json': OWNER.notApplicable,
  'dto/central/ResendRateLimitedDetail.json': OWNER.generated,
  'dto/central/ResendRateLimitedErrorBody.json': OWNER.generated,
  'dto/central/ResendVerificationRequest.json': OWNER.generated,
  'dto/central/ResendVerificationResponse.json': OWNER.generated,
  'dto/central/ResetErrorBody.json': OWNER.generated,
  'dto/central/ResetErrorDetail.json': OWNER.generated,
  'dto/central/ResetIpRateLimitedDetail.json': OWNER.generated,
  'dto/central/ResetIpRateLimitedErrorBody.json': OWNER.generated,
  'dto/central/ResetPasswordRequest.json': OWNER.generated,
  'dto/central/ResetPasswordResponse.json': OWNER.generated,
  'dto/central/ShortcutDeletedPayload.json': OWNER.shortcutsRemoved,
  'dto/central/ShortcutDto.json': OWNER.shortcutsRemoved,
  'dto/central/TrustPinResponse.json': OWNER.generated,
  'dto/central/UpdateCustomAgentTypeRequest.json': OWNER.customTypes,
  'dto/central/UpdateShortcutRequest.json': OWNER.shortcutsRemoved,
  'dto/central/UpdateTemplateRequest.json': OWNER.generated,
  'dto/central/UserAuthResponse.json': OWNER.generated,
  'dto/central/UserLoginRequest.json': OWNER.generated,
  'dto/central/UserRegisterRequest.json': OWNER.generated,
  'dto/central/VerificationRequiredDetail.json': OWNER.generated,
  'dto/central/VerificationRequiredErrorBody.json': OWNER.generated,
  'dto/central/VerifyEmailRequest.json': OWNER.generated,
};
