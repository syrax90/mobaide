/**
 * Workspace wire shapes, pinned by `tests/protocol_fixtures/dto/agent/ProjectInfo.json`.
 *
 * Hand-written rather than taken from the generated agent schema because
 * `projects.changed` carries this shape over an untrusted socket and it is
 * written straight into the query cache — the same reason `session.ts` exists.
 * The REST list decodes through the identical schema so both writers agree
 * byte-for-byte.
 */
import { z } from 'zod';
import { optionalWireString } from '../wireSchema';

/**
 * A `default_flags` value. Heterogeneous on the wire by design
 * (`FlagValue = str | int | float | bool`) — the map is passed to the CLI
 * verbatim, so a flag typed as a number must not arrive as a string.
 */
export const flagValueSchema = z.union([z.string(), z.number(), z.boolean()]);

/**
 * The per-workspace Auto Scan filter.
 *
 * Every field has a server-side default, so a partial object on the wire fills
 * the rest in — and an absent filter materialises the whole thing. There is no
 * "clear" state: the scanner always needs a complete filter, which is why the
 * update path replaces it wholesale instead of merging fields.
 */
export function defaultAutoScanFilter() {
  return {
    state_names: ['Proposals'],
    label_names: ['AutoPipeline'],
    active_state: 'In Progress',
    done_state: 'Done',
    removal_label: 'AutoPipeline',
  };
}

export const autoScanFilterSchema = z.looseObject({
  state_names: z.array(z.string()).default(() => defaultAutoScanFilter().state_names),
  label_names: z.array(z.string()).default(() => defaultAutoScanFilter().label_names),
  active_state: z.string().default('In Progress'),
  done_state: z.string().default('Done'),
  removal_label: z.string().default('AutoPipeline'),
});

/**
 * The per-workspace GitHub filter (`GitHubScanFilterModel`): 1–20 trigger
 * labels, an optional assignee (`@me` is the signed-in `gh` account) and an
 * optional label the scan adds when it takes an issue.
 */
export const githubScanFilterSchema = z.looseObject({
  labels: z.array(z.string()).default([]),
  assignee: optionalWireString(),
  active_label: optionalWireString(),
});

/**
 * `auto_scan`: the last Auto Scan choices of a workspace, for the wizard to
 * pre-fill (Delta 2026-11-18). Every field is absent or `null` until a scan
 * start sets it, and a `null` filter means nobody chose one, so the wizard
 * starts that filter empty rather than at the legacy defaults.
 *
 * `source` and `cli` stay open strings, as every kind on the wire does; the
 * wizard ignores a value it does not offer.
 */
export const autoScanSettingsSchema = z.looseObject({
  source: optionalWireString(),
  cli: optionalWireString(),
  linear_filter: autoScanFilterSchema.nullish().transform((value) => value ?? undefined),
  github_filter: githubScanFilterSchema.nullish().transform((value) => value ?? undefined),
});

/**
 * One workspace as the agent publishes it (`WorkspaceReadModel` /
 * `workspaces_store._record_to_public`).
 *
 * `linear_api_key` is deliberately absent: the server never echoes it, and
 * `has_linear_key` is the only signal a client gets. That is what makes a
 * delete-then-recreate undo lossy, and why web defers the delete instead.
 */
export const workspaceInfoSchema = z.looseObject({
  name: z.string(),
  path: z.string(),
  conda_env: optionalWireString(),
  default_flags: z.record(z.string(), flagValueSchema).default({}),
  order: z.number().default(0),
  has_linear_key: z.boolean().default(false),
  is_git: z.boolean().default(false),
  path_exists: z.boolean().default(true),
  auto_scan_filter: autoScanFilterSchema.default(defaultAutoScanFilter),
  /** Seeded by the previous scan start; `null` means "use the engine default". */
  auto_scan_prompt: optionalWireString(),
  /** Absent on an agent from before the delta, which the wizard reads as "nothing chosen". */
  auto_scan: autoScanSettingsSchema
    .nullish()
    .transform((value) => value ?? undefined)
    .optional(),
});

export type WorkspaceInfo = z.infer<typeof workspaceInfoSchema>;
export type AutoScanFilter = z.infer<typeof autoScanFilterSchema>;
export type GitHubScanFilter = z.infer<typeof githubScanFilterSchema>;
export type AutoScanSettings = z.infer<typeof autoScanSettingsSchema>;
export type FlagValue = z.infer<typeof flagValueSchema>;

/**
 * `projects.changed` — the agent's **complete** workspace list, republished after
 * every create, update, delete and reorder, and again on every mobile
 * events-socket connect (the relay's `request_events_snapshot` control message
 * makes the agent re-publish it). Authoritative: names missing from it are gone.
 */
export const workspacesChangedSchema = z.looseObject({
  projects: z.array(workspaceInfoSchema),
});

/**
 * `git.changed` — a pointer, not a payload. The agent names the workspace whose
 * branch/worktree tree changed and the client re-reads `…/git/items`; there is
 * no snapshot to write.
 *
 * `toplevel` / `common_dir` are the workspace's resolved repository root and
 * common git dir (`null` once it is no longer a repo), so a session-scoped
 * Changes viewer whose repo matches either can drop its cache even when the
 * session carries no `project_name` — a linked worktree shares the common dir.
 */
export const gitChangedSchema = z.looseObject({
  project_name: z.string(),
  toplevel: optionalWireString(),
  common_dir: optionalWireString(),
});

export type GitChanged = z.infer<typeof gitChangedSchema>;
