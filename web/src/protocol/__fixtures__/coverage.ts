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
  shortcuts: 'WEB-06 — keyboard & saved shortcuts',
  autoscan: 'WEB-10 — auto-scan monitor, notifications & settings',
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
   */
  generated: 'shipped — typed by the generated OpenAPI schema',
  /** Editing a custom agent type on web: CLA-231. */
  customTypes: 'CLA-231 — custom agent type editing on web',
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
  'frames/terminal_frames.json',
  'frames/autoscan_frames.json',
  'pairing/qr_payload.json',
  'relay/relay_mapping.json',
  'dto/agent/AntigravityOptionsModel.json',
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
  'dto/central/ShortcutDeletedPayload.json',
  'dto/central/ShortcutDto.json',
  'dto/central/TemplateDeletedPayload.json',
  'dto/central/TemplateDto.json',
];

/** Fixture files no TypeScript test reads yet, each with the milestone that will. */
export const DEFERRED_FIXTURES: Readonly<Record<string, string>> = {
  /**
   * Re-pointed from WEB-05 during that milestone: this is the golden for the
   * mobile clients' `ui/autoscan/AnsiParser`, which renders the auto-scan log
   * pane. The terminal screen has no SGR parser of its own — xterm.js owns
   * that — so the web consumer is the auto-scan log view.
   */
  'ansi/ansi_sgr.json': OWNER.autoscan,

  'dto/agent/AutoScanFilterDto.json': OWNER.autoscan,
  'dto/agent/AutoScanInstanceDto.json': OWNER.autoscan,
  'dto/agent/AutoScanListResponse.json': OWNER.autoscan,
  'dto/agent/CodexLoginRequest.json': OWNER.generated,
  'dto/agent/CodexLoginResponse.json': OWNER.generated,
  'dto/agent/ForceUpdateResponse.json': OWNER.autoscan,
  'dto/agent/GitDeleteItem.json': OWNER.generated,
  'dto/agent/GitDeleteRequest.json': OWNER.generated,
  'dto/agent/GitDeleteResponse.json': OWNER.generated,
  'dto/agent/GitDeleteResultItem.json': OWNER.generated,
  'dto/agent/GitItem.json': OWNER.generated,
  'dto/agent/GitItemsResponse.json': OWNER.generated,
  'dto/agent/LinearLabelDto.json': OWNER.autoscan,
  'dto/agent/LinearStateDto.json': OWNER.autoscan,
  'dto/agent/ProjectOrderRequest.json': OWNER.generated,
  'dto/agent/ProjectUpdate.json': OWNER.generated,
  'dto/agent/ProjectWrite.json': OWNER.generated,
  'dto/agent/RefreshRequest.json': OWNER.generated,
  'dto/agent/RunNowRequest.json': OWNER.autoscan,
  'dto/agent/RunNowResponse.json': OWNER.autoscan,
  'dto/agent/ScrollbackResponse.json': OWNER.unusedByClients,
  'dto/agent/StartScanRequest.json': OWNER.autoscan,
  'dto/agent/StopScanRequest.json': OWNER.autoscan,
  'dto/agent/TokenRequest.json': OWNER.generated,
  'dto/agent/TokenResponse.json': OWNER.generated,

  'dto/central/AgentSummaryResponse.json': OWNER.generated,
  'dto/central/CancelAccountDeletionResponse.json': OWNER.generated,
  'dto/central/CreateAgentRequest.json': OWNER.generated,
  'dto/central/CreateAgentResponse.json': OWNER.generated,
  'dto/central/CreateShortcutRequest.json': OWNER.shortcuts,
  'dto/central/CreateTemplateRequest.json': OWNER.generated,
  'dto/central/DeleteAccountReauthDetail.json': OWNER.generated,
  'dto/central/DeleteAccountReauthErrorBody.json': OWNER.generated,
  'dto/central/DeleteAccountRequest.json': OWNER.generated,
  'dto/central/DeviceResponse.json': OWNER.generated,
  'dto/central/ForgotPasswordRequest.json': OWNER.generated,
  'dto/central/ForgotPasswordResponse.json': OWNER.generated,
  'dto/central/GoogleAuthRequest.json': OWNER.generated,
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
  'dto/central/UpdateCustomAgentTypeRequest.json': OWNER.customTypes,
  'dto/central/UpdateShortcutRequest.json': OWNER.shortcuts,
  'dto/central/UpdateTemplateRequest.json': OWNER.generated,
  'dto/central/UserAuthResponse.json': OWNER.generated,
  'dto/central/UserLoginRequest.json': OWNER.generated,
  'dto/central/UserRegisterRequest.json': OWNER.generated,
  'dto/central/VerificationRequiredDetail.json': OWNER.generated,
  'dto/central/VerificationRequiredErrorBody.json': OWNER.generated,
  'dto/central/VerifyEmailRequest.json': OWNER.generated,
};
