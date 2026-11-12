/**
 * Outbound create-session wire shapes, pinned by
 * `tests/protocol_fixtures/dto/agent/{CreateSessionRequest,ClaudeOptionsModel,
 * CodexOptionsModel,AntigravityOptionsModel,OpenCodeOptionsModel}.json`.
 *
 * **These are `z.strictObject`, deliberately breaking the `looseObject` rule in
 * `../wireSchema`.** That rule protects *inbound* data, where an unknown key is
 * a newer server and dropping it would break forward compatibility. These are
 * *outbound requests*, where an unknown key is a form field that leaked out of
 * a branch the user is no longer on — the exact failure the wizard's per-mode
 * buckets exist to prevent. `z.object` would strip it silently; `strictObject`
 * makes it throw.
 *
 * `resolved_command` / `resolved_name` are deliberately absent: they are
 * server-only. The relay strips any client-supplied values before injecting the
 * stored ones (`central_server/relay/mobile_proxy.py::_resolve_custom_kind`);
 * omitting them here is the client-side half of the same rule.
 */
import { z } from 'zod';
import { optionalWireString } from '../wireSchema';

export const CLAUDE_EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
export const CODEX_SANDBOXES = ['read-only', 'workspace-write', 'danger-full-access'] as const;
export const CODEX_APPROVALS = ['untrusted', 'on-request', 'never'] as const;
export const CODEX_REASONING_EFFORTS = ['minimal', 'low', 'medium', 'high'] as const;
export const SESSION_UIS = ['terminal', 'chat'] as const;

export const claudeOptionsSchema = z.strictObject({
  plan_mode: z.boolean().default(false),
  no_memory: z.boolean().default(false),
  /**
   * The **wire-absence** default, mirroring `agent/sessions/models.py:30` and
   * `core/claude_launcher.py:109` — *not* the UI default, which starts off.
   *
   * An omitted `skip_permissions` means `true`, i.e. the agent launches with
   * `--dangerously-skip-permissions`. The server keeps it that way on purpose
   * so an old client's silence is not reinterpreted. The consequence for every
   * client is the same: **always send this field explicitly.**
   */
  skip_permissions: z.boolean().default(true),
  initial_prompt: optionalWireString(),
  worktree: z.boolean().default(false),
  branch_name: optionalWireString(),
  base_branch: optionalWireString(),
  identifier: optionalWireString(),
  title: optionalWireString(),
  model: optionalWireString(),
  effort: z
    .enum(CLAUDE_EFFORTS)
    .nullish()
    .transform((value) => value ?? undefined),
  fast_mode: z.boolean().default(false),
  ultracode: z.boolean().default(false),
  append_system_prompt: optionalWireString(),
  /**
   * Emits `--debug`. Unlike `skip_permissions` above, the wire-absence default
   * matches the UI default (both false), so an omitted field is unambiguous.
   */
  debug: z.boolean().default(false),
  /**
   * Claude Code's `viewMode`: `true` → focus, `false` → default, absent → the
   * agent leaves the CLI's own view setting alone. Clients always send a bool.
   */
  focus_mode: z.boolean().nullish(),
});

export const codexOptionsSchema = z.strictObject({
  model: optionalWireString(),
  sandbox: z.enum(CODEX_SANDBOXES).default('workspace-write'),
  /**
   * Wire default `never`; the wizard's UI default is `on-request` (both mobile
   * clients). They differ on purpose, which is the second reason every boolean
   * and enum goes on the wire explicitly.
   */
  approval: z.enum(CODEX_APPROVALS).default('never'),
  /**
   * Three-state, and the one field that must not use `optionalWireString`'s
   * absent-and-null-both-become-undefined rule: absent means the server default
   * `high`, an explicit `null` means "emit no `--reasoning-effort` flag at all",
   * and a value overrides. Collapsing null into undefined would lose the middle
   * state — `explicit_null_reasoning_effort_round_trips_as_null` pins it.
   *
   * Free-form string, not `z.enum(CODEX_REASONING_EFFORTS)`: the vocabulary is
   * agent-enumerated per model (`capabilities.cli.codex.model_details`), so a
   * closed enum would reject efforts the agent itself advertised (e.g. `ultra`)
   * — `catalog_effort_passes_free_form` pins it. `CODEX_REASONING_EFFORTS` is
   * purely the UI's fallback option list.
   */
  reasoning_effort: z.string().nullable().default('high'),
  full_auto: z.boolean().default(false),
  initial_prompt: optionalWireString(),
  worktree: z.boolean().default(false),
  branch_name: optionalWireString(),
  base_branch: optionalWireString(),
});

export const antigravityOptionsSchema = z
  .strictObject({
    model: optionalWireString(),
    skip_permissions: z.boolean().default(false),
    sandbox: z.boolean().default(false),
    add_dirs: z.array(z.string()).default([]),
    worktree: z.boolean().default(false),
    branch_name: optionalWireString(),
  base_branch: optionalWireString(),
  })
  .refine((options) => !(options.skip_permissions && options.sandbox), {
    // Mirrors the server's `_reject_sandboxed_skip_permissions` 422
    // (`agent/sessions/models.py`): --dangerously-skip-permissions
    // auto-approves sandbox escapes, so the pair is refused outright.
    error: 'skip_permissions and sandbox are mutually exclusive',
    path: ['skip_permissions'],
  });

export const opencodeOptionsSchema = z.strictObject({
  auto_approve: z.boolean().default(false),
  initial_prompt: optionalWireString(),
  worktree: z.boolean().default(false),
  branch_name: optionalWireString(),
  base_branch: optionalWireString(),
});

export const createSessionRequestSchema = z.strictObject({
  command: z.array(z.string()).nullish().transform((value) => value ?? undefined),
  cwd: optionalWireString(),
  env: z
    .record(z.string(), z.string())
    .nullish()
    .transform((value) => value ?? undefined),
  rows: z.number().int().default(24),
  cols: z.number().int().default(80),
  project_name: optionalWireString(),
  /**
   * Which CLI to launch. Open, not an enum: `custom_<12hex>` is a valid value
   * and the set grows server-side. Shell sessions omit it.
   */
  cli: optionalWireString(),
  claude_options: claudeOptionsSchema.nullish().transform((value) => value ?? undefined),
  codex_options: codexOptionsSchema.nullish().transform((value) => value ?? undefined),
  antigravity_options: antigravityOptionsSchema
    .nullish()
    .transform((value) => value ?? undefined),
  opencode_options: opencodeOptionsSchema.nullish().transform((value) => value ?? undefined),
  /** Terminal/custom worktree intent. Managed CLIs carry theirs inside their options block. */
  worktree: z.boolean().default(false),
  branch_name: optionalWireString(),
  ui: z
    .enum(SESSION_UIS)
    .nullish()
    .transform((value) => value ?? undefined),
  /** Chat only: the agent's stored session to continue instead of starting a new one. */
  chat_resume_id: z
    .string()
    .max(200)
    .regex(/^[A-Za-z0-9._:-]+$/)
    .nullish()
    .transform((value) => value ?? undefined),
  base_branch: optionalWireString(),
});

export type ClaudeOptionsWire = z.infer<typeof claudeOptionsSchema>;
export type CodexOptionsWire = z.infer<typeof codexOptionsSchema>;
export type AntigravityOptionsWire = z.infer<typeof antigravityOptionsSchema>;
export type OpenCodeOptionsWire = z.infer<typeof opencodeOptionsSchema>;
export type CreateSessionRequestWire = z.infer<typeof createSessionRequestSchema>;
