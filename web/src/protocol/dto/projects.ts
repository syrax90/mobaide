/**
 * Project wire shapes, pinned by `tests/protocol_fixtures/dto/agent/ProjectInfo.json`.
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
 * The per-project Auto Scan filter.
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
 * One project as the agent publishes it (`ProjectReadModel` /
 * `projects_store._record_to_public`).
 *
 * `linear_api_key` is deliberately absent: the server never echoes it, and
 * `has_linear_key` is the only signal a client gets. That is what makes a
 * delete-then-recreate undo lossy, and why web defers the delete instead.
 */
export const projectInfoSchema = z.looseObject({
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
});

export type ProjectInfo = z.infer<typeof projectInfoSchema>;
export type AutoScanFilter = z.infer<typeof autoScanFilterSchema>;
export type FlagValue = z.infer<typeof flagValueSchema>;

/**
 * `projects.changed` — the agent's **complete** project list, republished after
 * every create, update, delete and reorder, and again on every mobile
 * events-socket connect (the relay's `request_events_snapshot` control message
 * makes the agent re-publish it). Authoritative: names missing from it are gone.
 */
export const projectsChangedSchema = z.looseObject({
  projects: z.array(projectInfoSchema),
});

/**
 * `git.changed` — a pointer, not a payload. The agent names the project whose
 * branch/worktree tree changed and the client re-reads `…/git/items`; there is
 * no snapshot to write.
 *
 * `toplevel` / `common_dir` are the project's resolved repository root and
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
